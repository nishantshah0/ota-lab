#include "update.h"
#include "ota_proto.h"
#include "layout.h"
#include "flash.h"
#include "crc32.h"
#include "image.h"
#include "journal.h"
#include "progress.h"
#include "uart.h"
#include "fmt.h"
#include "stm32f4_regs.h"

#define QUEUE_LEN        64U   /* power of two, frames buffered between ISR and main loop */
#define INACTIVITY_MS    10000U

/* ISR -> main loop queue */
static struct can_frame  queue[QUEUE_LEN];
static volatile uint32_t q_head, q_tail;
static volatile uint32_t rx_frames, rx_dropped;
static uint32_t dbg_last_seq, dbg_dups, dbg_naks, dbg_accepted;

static struct {
    uint8_t  state;
    uint8_t  running_slot;
    uint8_t  slot;            /* target */
    uint8_t  flags;
    uint32_t image_size;
    uint32_t header_crc;
    uint8_t  ver[3];          /* claimed by START_A, checked again from the header */
    uint16_t total_chunks;
    uint16_t next_seq;        /* chunks safely in flash */
    uint16_t fill;            /* chunks in the RAM window */
    uint8_t  window[OTA_WINDOW_BYTES];
    uint16_t last_nak;        /* rate limit NAKs for the same gap */
    uint32_t idle_ms;
    bool     have_start_a;
} ctx;

static const char *code_str(uint8_t code)
{
    switch (code) {
    case OTA_OK:              return "OK";
    case OTA_ERR_GAP:         return "GAP";
    case OTA_ERR_NOT_STARTED: return "NOT_STARTED";
    case OTA_ERR_BAD_SIZE:    return "BAD_SIZE";
    case OTA_ERR_SLOT_BUSY:   return "SLOT_BUSY";
    case OTA_ERR_VERSION_LOW: return "VERSION_LOW";
    case OTA_ERR_FLASH:       return "FLASH";
    case OTA_ERR_INCOMPLETE:  return "INCOMPLETE";
    case OTA_ERR_BAD_MAGIC:   return "BAD_MAGIC";
    case OTA_ERR_BAD_HEADER:  return "BAD_HEADER";
    case OTA_ERR_WRONG_SLOT:  return "WRONG_SLOT";
    case OTA_ERR_BAD_CRC:     return "BAD_CRC";
    case OTA_ERR_BAD_SIG:     return "BAD_SIGNATURE";
    case OTA_ERR_BAD_VECTORS: return "BAD_VECTORS";
    case OTA_ERR_JOURNAL:     return "JOURNAL";
    case OTA_ERR_TIMEOUT:     return "TIMEOUT";
    case OTA_ERR_SEQUENCE:    return "SEQUENCE";
    default:                  return "?";
    }
}

static void reply(uint8_t type, uint16_t seq, uint8_t code, uint32_t detail)
{
    struct can_frame f = { .id = OTA_ID_REPLY, .extended = false, .remote = false, .dlc = 8 };
    f.data[0] = type;
    if (type == OTA_REPLY_VERDICT) {
        f.data[1] = code;
        f.data[2] = (uint8_t)detail;
        f.data[3] = (uint8_t)(detail >> 8);
        f.data[4] = (uint8_t)(detail >> 16);
        f.data[5] = (uint8_t)(detail >> 24);
    } else {
        f.data[1] = (uint8_t)seq;
        f.data[2] = (uint8_t)(seq >> 8);
        f.data[3] = code;
    }
    (void)can_send(&f);
}

static void log_line(const char *what, uint8_t code, uint32_t n)
{
    uart_puts("update: ");
    uart_puts(what);
    if (code != OTA_OK || what[0] == 'F') {
        uart_puts(" code=");
        uart_puts(code_str(code));
    }
    uart_puts(" n=");
    fmt_put_udec(n);
    uart_puts("\r\n");
}

void update_init(uint8_t running_slot)
{
    ctx.state = OTA_STATE_IDLE;
    ctx.running_slot = running_slot;
    ctx.have_start_a = false;
    q_head = q_tail = 0;

    struct progress p;
    if (progress_read(&p) && p.state == OTA_STATE_RECEIVING) {
        uart_puts("update: resumable transfer on flash slot=");
        uart_puts(p.slot == SLOT_A ? "A" : "B");
        uart_puts(" chunks=");
        fmt_put_udec(p.chunks);
        uart_puts("\r\n");
    }
}

bool update_enqueue(const struct can_frame *f)
{
    if (f->extended || (f->id != OTA_ID_CTRL && f->id != OTA_ID_DATA)) {
        return false;
    }
    rx_frames++;
    if ((q_head - q_tail) < QUEUE_LEN) {
        queue[q_head & (QUEUE_LEN - 1U)] = *f;
        q_head++;
    } else {
        rx_dropped++;
    }
    return true; /* ours even if dropped: never echo protocol frames */
}

static uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint32_t running_version(void)
{
    return image_version_packed(image_self_header());
}

static uint32_t packed(const uint8_t v[3])
{
    return ((uint32_t)v[0] << 16) | ((uint32_t)v[1] << 8) | v[2];
}

static bool write_progress(uint8_t state)
{
    struct progress p = {
        .header_crc = ctx.header_crc,
        .image_size = ctx.image_size,
        .slot       = ctx.slot,
        .state      = state,
        .chunks     = ctx.next_seq,
        .flags      = ctx.flags,
    };
    return progress_write(&p);
}

static void handle_start_a(const struct can_frame *f)
{
    ctx.image_size = rd32(f->data + 1);
    ctx.ver[0] = f->data[5];
    ctx.ver[1] = f->data[6];
    ctx.ver[2] = f->data[7];
    ctx.have_start_a = true;
}

static void handle_start_b(const struct can_frame *f)
{
    if (!ctx.have_start_a) {
        reply(OTA_REPLY_NAK, 0, OTA_ERR_SEQUENCE, 0);
        return;
    }
    ctx.have_start_a = false;
    ctx.flags = f->data[1];
    uint8_t slot = f->data[2];
    uint32_t header_crc = rd32(f->data + 3);

    uint8_t code = OTA_OK;
    if (slot != SLOT_A && slot != SLOT_B) {
        code = OTA_ERR_WRONG_SLOT;
    } else if (slot == ctx.running_slot) {
        code = OTA_ERR_SLOT_BUSY;
    } else if (ctx.image_size <= IMAGE_HEADER_SIZE || ctx.image_size > SLOT_SIZE || (ctx.image_size & 3U) != 0U) {
        code = OTA_ERR_BAD_SIZE;
    } else if (packed(ctx.ver) < running_version() && (ctx.flags & OTA_START_FLAG_FORCE) == 0U) {
        code = OTA_ERR_VERSION_LOW;   /* early, advisory; the signed header decides at FINISH */
    }
    if (code != OTA_OK) {
        log_line("START rejected", code, ctx.image_size);
        reply(OTA_REPLY_NAK, 0, code, 0);
        ctx.state = OTA_STATE_IDLE;
        return;
    }

    ctx.slot = slot;
    ctx.header_crc = header_crc;
    ctx.total_chunks = (uint16_t)((ctx.image_size + OTA_CHUNK_BYTES - 1U) / OTA_CHUNK_BYTES);
    ctx.fill = 0;
    ctx.last_nak = 0xFFFFU;
    ctx.idle_ms = 0;

    /* Resume if flash holds a matching, unfinished transfer. */
    struct progress p;
    bool resume = progress_read(&p) && p.state == OTA_STATE_RECEIVING &&
                  p.header_crc == header_crc && p.image_size == ctx.image_size &&
                  p.slot == slot && p.chunks < ctx.total_chunks;
    if (resume) {
        ctx.next_seq = p.chunks;
        ctx.flags |= p.flags & OTA_START_FLAG_FORCE ? 0U : 0U;
        ctx.state = OTA_STATE_RECEIVING;
        log_line("START resume", OTA_OK, ctx.next_seq);
        reply(OTA_REPLY_ACK, ctx.next_seq, 1, 0);
        return;
    }

    if (!flash_erase_sector(slot_sector(slot))) {
        log_line("START erase failed", OTA_ERR_FLASH, 0);
        reply(OTA_REPLY_NAK, 0, OTA_ERR_FLASH, 0);
        return;
    }
    ctx.next_seq = 0;
    ctx.state = OTA_STATE_RECEIVING;
    if (!write_progress(OTA_STATE_RECEIVING)) {
        reply(OTA_REPLY_NAK, 0, OTA_ERR_FLASH, 0);
        ctx.state = OTA_STATE_IDLE;
        return;
    }
    log_line("START fresh", OTA_OK, ctx.total_chunks);
    reply(OTA_REPLY_ACK, 0, 0, 0);
}

static uint32_t chunk_len(uint16_t seq)
{
    uint32_t off = (uint32_t)seq * OTA_CHUNK_BYTES;
    uint32_t rem = ctx.image_size - off;
    return rem < OTA_CHUNK_BYTES ? rem : OTA_CHUNK_BYTES;
}

static bool flush_window(void)
{
    uint32_t addr = slot_base(ctx.slot) + (uint32_t)ctx.next_seq * OTA_CHUNK_BYTES;
    uint32_t bytes = 0;
    for (uint16_t i = 0; i < ctx.fill; i++) {
        bytes += chunk_len((uint16_t)(ctx.next_seq + i));
    }
    /* Window boundaries and the image size are multiples of 4, so bytes is too. */
    if (!flash_program(addr, ctx.window, bytes)) {
        return false;
    }
    ctx.next_seq = (uint16_t)(ctx.next_seq + ctx.fill);
    ctx.fill = 0;
    return write_progress(OTA_STATE_RECEIVING);
}

static void handle_data(const struct can_frame *f)
{
    if (ctx.state != OTA_STATE_RECEIVING) {
        reply(OTA_REPLY_NAK, 0, OTA_ERR_NOT_STARTED, 0);
        return;
    }
    uint16_t seq = (uint16_t)(f->data[0] | (f->data[1] << 8));
    uint16_t expected = (uint16_t)(ctx.next_seq + ctx.fill);
    ctx.idle_ms = 0;
    dbg_last_seq = seq;

    if (seq != expected) {
        if (seq > expected && ctx.last_nak != expected) {
            ctx.last_nak = expected;
            dbg_naks++;
            reply(OTA_REPLY_NAK, expected, OTA_ERR_GAP, 0);
        } else {
            dbg_dups++;
        }
        return; /* duplicates of already-held chunks are silently dropped */
    }
    ctx.last_nak = 0xFFFFU;
    dbg_accepted++;

    uint32_t len = chunk_len(seq);
    for (uint32_t i = 0; i < len; i++) {
        ctx.window[ctx.fill * OTA_CHUNK_BYTES + i] = f->data[2 + i];
    }
    ctx.fill++;

    bool last = (uint32_t)(seq + 1U) == ctx.total_chunks;
    if (ctx.fill == OTA_WINDOW_CHUNKS || last) {
        if (!flush_window()) {
            log_line("DATA flash failed", OTA_ERR_FLASH, ctx.next_seq);
            reply(OTA_REPLY_NAK, ctx.next_seq, OTA_ERR_FLASH, 0);
            ctx.state = OTA_STATE_IDLE;
            return;
        }
        reply(OTA_REPLY_ACK, ctx.next_seq, 0, 0);
    }
}

static uint8_t map_result(enum image_result r)
{
    switch (r) {
    case IMAGE_OK:            return OTA_OK;
    case IMAGE_ERR_MAGIC:     return OTA_ERR_BAD_MAGIC;
    case IMAGE_ERR_VERSION:   return OTA_ERR_BAD_HEADER;
    case IMAGE_ERR_SIZE:      return OTA_ERR_BAD_SIZE;
    case IMAGE_ERR_SLOT:      return OTA_ERR_WRONG_SLOT;
    case IMAGE_ERR_CRC:       return OTA_ERR_BAD_CRC;
    case IMAGE_ERR_SIGNATURE: return OTA_ERR_BAD_SIG;
    case IMAGE_ERR_VECTORS:   return OTA_ERR_BAD_VECTORS;
    default:                  return OTA_ERR_BAD_HEADER;
    }
}

static void handle_finish(void)
{
    if (ctx.state != OTA_STATE_RECEIVING) {
        reply(OTA_REPLY_VERDICT, 0, OTA_ERR_NOT_STARTED, 0);
        return;
    }
    if (ctx.next_seq != ctx.total_chunks || ctx.fill != 0U) {
        log_line("FINISH", OTA_ERR_INCOMPLETE, ctx.next_seq);
        reply(OTA_REPLY_VERDICT, 0, OTA_ERR_INCOMPLETE, ctx.next_seq);
        return;
    }

    /* Everything is in flash: validate it exactly as the bootloader will. */
    enum image_result r = image_validate(ctx.slot);
    uint8_t code = map_result(r);
    uint32_t detail = 0;
    if (code == OTA_OK) {
        const struct image_header *h = (const struct image_header *)slot_base(ctx.slot);
        uint32_t incoming = image_version_packed(h);
        detail = incoming;
        if (h->image_size + IMAGE_HEADER_SIZE != ctx.image_size) {
            code = OTA_ERR_BAD_SIZE;
        } else if (incoming < running_version() && (ctx.flags & OTA_START_FLAG_FORCE) == 0U) {
            code = OTA_ERR_VERSION_LOW;   /* the signed header is the authority */
        }
    }

    if (code == OTA_OK) {
        struct boot_state s;
        journal_read(&s);
        s.pending = ctx.slot;
        s.attempts = 0;
        s.confirmed = 0;
        if (!journal_write(&s)) {
            code = OTA_ERR_JOURNAL;
        } else {
            detail = s.seq;
        }
    }

    write_progress(code == OTA_OK ? OTA_STATE_DONE : PROGRESS_STATE_ABORTED);
    ctx.state = code == OTA_OK ? OTA_STATE_DONE : OTA_STATE_IDLE;
    log_line("FINISH", code, detail);
    reply(OTA_REPLY_VERDICT, 0, code, detail);
}

static void handle_abort(void)
{
    if (ctx.state == OTA_STATE_RECEIVING) {
        write_progress(PROGRESS_STATE_ABORTED);
    }
    ctx.state = OTA_STATE_IDLE;
    ctx.have_start_a = false;
    log_line("ABORT", OTA_OK, ctx.next_seq);
    reply(OTA_REPLY_ACK, ctx.next_seq, 0, 0);
}

static void send_status(void)
{
    struct can_frame f = { .id = OTA_ID_REPLY, .extended = false, .remote = false, .dlc = 8 };
    f.data[0] = OTA_REPLY_STATUS;
    f.data[1] = ctx.state;
    f.data[2] = ctx.slot;
    f.data[3] = (uint8_t)ctx.next_seq;
    f.data[4] = (uint8_t)(ctx.next_seq >> 8);
    f.data[5] = (uint8_t)ctx.total_chunks;
    f.data[6] = (uint8_t)(ctx.total_chunks >> 8);
    f.data[7] = 0xFF;
    (void)can_send(&f);
}

static void handle_frame(const struct can_frame *f)
{
    if (f->id == OTA_ID_DATA) {
        if (f->dlc >= 2U) {
            handle_data(f);
        }
        return;
    }
    if (f->dlc < 1U) {
        return;
    }
    switch (f->data[0]) {
    case OTA_CTRL_START_A: if (f->dlc == 8U) handle_start_a(f); break;
    case OTA_CTRL_START_B: if (f->dlc == 8U) handle_start_b(f); break;
    case OTA_CTRL_FINISH:  handle_finish(); break;
    case OTA_CTRL_ABORT:   handle_abort(); break;
    case OTA_CTRL_STATUS:  send_status(); break;
    default: break;
    }
}

void update_poll(void)
{
    while (q_tail != q_head) {
        struct can_frame f = queue[q_tail & (QUEUE_LEN - 1U)];
        q_tail++;
        handle_frame(&f);
    }
}

void update_tick_10ms(void)
{
    if (ctx.state == OTA_STATE_RECEIVING) {
        ctx.idle_ms += 10U;
        if (ctx.idle_ms >= INACTIVITY_MS) {
            /* Back to idle; the progress record stays RECEIVING so a later
             * START with the same image resumes. Runs in ISR context: only
             * touch RAM state here, the log line comes from the main loop. */
            ctx.state = OTA_STATE_IDLE;
            ctx.fill = 0;
            ctx.idle_ms = 0;
        }
    }
}

void update_print_status(void)
{
    uart_puts("UPDATE state=");
    uart_puts(ctx.state == OTA_STATE_IDLE ? "IDLE" : ctx.state == OTA_STATE_RECEIVING ? "RECEIVING" : "DONE");
    uart_puts(" slot=");
    uart_puts(ctx.state == OTA_STATE_IDLE ? "none" : ctx.slot == SLOT_A ? "A" : "B");
    uart_puts(" next=");
    fmt_put_udec(ctx.next_seq);
    uart_puts(" total=");
    fmt_put_udec(ctx.total_chunks);
    uart_puts(" fill=");
    fmt_put_udec(ctx.fill);
    uart_puts(" rx_frames=");
    fmt_put_udec(rx_frames);
    uart_puts(" rx_dropped=");
    fmt_put_udec(rx_dropped);
    uart_puts(" last_seq=");
    fmt_put_udec(dbg_last_seq);
    uart_puts(" accepted=");
    fmt_put_udec(dbg_accepted);
    uart_puts(" dups=");
    fmt_put_udec(dbg_dups);
    uart_puts(" naks=");
    fmt_put_udec(dbg_naks);
    uart_puts(" progress_records=");
    fmt_put_udec(progress_count());
    struct progress p;
    if (progress_read(&p)) {
        uart_puts(" last_record=");
        uart_puts(p.state == OTA_STATE_RECEIVING ? "RECEIVING" : p.state == OTA_STATE_DONE ? "DONE" : "ABORTED");
        uart_puts("/");
        fmt_put_udec(p.chunks);
    }
    uart_puts("\r\n");
}

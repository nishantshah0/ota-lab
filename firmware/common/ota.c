#include "ota.h"
#include "layout.h"
#include "image.h"
#include "bootlog.h"
#include "uart.h"
#include "fmt.h"
#include "sysreset.h"
#include "update.h"

static struct ota_status st;

static const char *slot_name(uint8_t slot)
{
    switch (slot) {
    case SLOT_A:    return "A";
    case SLOT_B:    return "B";
    case SLOT_SAFE: return "SAFE";
    case SLOT_NONE: return "none";
    default:        return "?";
    }
}

void ota_init(uint8_t running_slot)
{
    st.running_slot = running_slot;
    st.has_header = (running_slot == SLOT_A || running_slot == SLOT_B);
    st.confirmed_now = false;
    journal_read(&st.state);
    st.is_pending = (st.state.pending == running_slot);
    st.is_active  = (st.state.active == running_slot);
}

const struct ota_status *ota_status(void)
{
    return &st;
}

bool ota_confirm(void)
{
    if (!st.has_header) {
        return false;
    }
    struct boot_state cur;
    journal_read(&cur);
    if (cur.active == st.running_slot && cur.pending == SLOT_NONE) {
        uart_puts("confirm: already active\r\n");
        return true;
    }
    struct boot_state next = cur;
    next.active    = st.running_slot;
    next.pending   = SLOT_NONE;
    next.attempts  = 0;
    next.confirmed = 1;
    if (!journal_write(&next)) {
        uart_puts("confirm: journal write FAILED\r\n");
        return false;
    }
    st.state = next;
    st.is_active = true;
    st.is_pending = false;
    st.confirmed_now = true;
    uart_puts("confirm: written seq=");
    fmt_put_udec(next.seq);
    uart_puts("\r\n");
    return true;
}

void ota_print_state(void)
{
    struct boot_state s;
    bool present = journal_read(&s);
    uart_puts("STATE seq=");
    fmt_put_udec(s.seq);
    uart_puts(" active=");
    uart_puts(slot_name(s.active));
    uart_puts(" pending=");
    uart_puts(slot_name(s.pending));
    uart_puts(" attempts=");
    fmt_put_udec(s.attempts);
    uart_puts(" confirmed=");
    fmt_put_udec(s.confirmed);
    uart_puts(present ? " journal=present" : " journal=empty");
    uart_puts(" bank0=");
    fmt_put_udec(journal_bank_used(0));
    uart_puts(" bank1=");
    fmt_put_udec(journal_bank_used(1));
    uart_puts("\r\n");
}

static void put_version(uint32_t v)
{
    fmt_put_udec((v >> 16) & 0xFFU);
    uart_putc('.');
    fmt_put_udec((v >> 8) & 0xFFU);
    uart_putc('.');
    fmt_put_udec(v & 0xFFU);
}

void ota_print_bootlog(void)
{
    unsigned n = bootlog_count();
    for (unsigned i = 0; i < n; i++) {
        struct bootlog_entry e;
        uart_puts("LOG idx=");
        fmt_put_udec(i);
        if (!bootlog_get(i, &e)) {
            uart_puts(" TORN\r\n");
            continue;
        }
        uart_puts(" seq=");
        fmt_put_udec(e.seq);
        uart_puts(" jseq=");
        fmt_put_udec(e.journal_seq);
        uart_puts(" slot=");
        uart_puts(slot_name(e.slot));
        uart_puts(" reason=");
        uart_puts(bootlog_reason_str(e.reason));
        uart_puts(" attempts=");
        fmt_put_udec(e.attempts);
        uart_puts(" cause=");
        uart_puts(bootlog_cause_str(e.cause));
        uart_puts(" a=");
        uart_puts(image_result_str((enum image_result)e.result_a));
        uart_puts(" b=");
        uart_puts(image_result_str((enum image_result)e.result_b));
        uart_puts(" ver=");
        put_version(e.version);
        uart_puts("\r\n");
    }
    uart_puts("LOG END n=");
    fmt_put_udec(n);
    uart_puts("\r\n");
}

void ota_print_version(void)
{
    uart_puts("VERSION slot=");
    uart_puts(slot_name(st.running_slot));
    if (st.has_header) {
        const struct image_header *h = image_self_header();
        uart_puts(" fw=");
        put_version(image_version_packed(h));
        uart_puts(" size=");
        fmt_put_udec(h->image_size);
    }
    uart_puts("\r\n");
}

static bool streq(const char *a, const char *b)
{
    while (*a != '\0' && *a == *b) {
        a++;
        b++;
    }
    return *a == *b;
}

void ota_console_line(const char *line)
{
    if (*line == '\0') {
        return;
    }
    if (streq(line, "log")) {
        ota_print_bootlog();
    } else if (streq(line, "state")) {
        ota_print_state();
    } else if (streq(line, "version")) {
        ota_print_version();
    } else if (streq(line, "confirm")) {
        ota_confirm();
    } else if (streq(line, "update")) {
        update_print_status();
    } else if (streq(line, "reboot")) {
        uart_puts("reboot: requested\r\n");
        system_reset();
    } else {
        uart_puts("ERR unknown command\r\n");
    }
}

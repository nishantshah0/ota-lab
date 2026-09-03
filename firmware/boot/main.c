/*
 * A/B bootloader, sectors 0..1.
 *
 * Reset -> read journal -> decide candidate slots -> validate (image_validate:
 * magic, header, size, slot, CRC, Ed25519 signature, vectors) -> record decision in the
 * boot log -> leave a marker in CCM -> start the watchdog -> jump.
 *
 * Decision rules (see docs/ARCHITECTURE.md for the flowchart):
 *   pending set, attempts >= MAX  -> roll back: clear pending, boot active only
 *   pending set, attempts <  MAX  -> attempts++, boot pending; if invalid,
 *                                    clear pending and boot active
 *   no pending                    -> boot active; if invalid, boot the other
 *   nothing valid                 -> safe-mode image
 */
#include <stdint.h>
#include <stdbool.h>

#include "stm32f4_regs.h"
#include "layout.h"
#include "image.h"
#include "journal.h"
#include "bootlog.h"
#include "boot_marker.h"
#include "crc32.h"
#include "iwdg.h"
#include "uart.h"
#include "fmt.h"

#define MAX_ATTEMPTS      3U
#define WATCHDOG_MS       1000U
#define SRAM_BASE         0x20000000U
#define SRAM_END          0x20020000U

static const char *slot_name(uint8_t slot)
{
    return slot == SLOT_A ? "A" : slot == SLOT_B ? "B" : slot == SLOT_SAFE ? "SAFE" : "none";
}

static void put_version(const struct image_header *h)
{
    uart_putc('v');
    fmt_put_udec(h->ver_major);
    uart_putc('.');
    fmt_put_udec(h->ver_minor);
    uart_putc('.');
    fmt_put_udec(h->ver_patch);
}

static void print_slot_result(uint8_t slot, enum image_result r)
{
    uart_puts("slot ");
    uart_puts(slot_name(slot));
    uart_puts(": ");
    uart_puts(image_result_str(r));
    if (r == IMAGE_OK) {
        uart_putc(' ');
        put_version((const struct image_header *)slot_base(slot));
    }
    uart_puts("\r\n");
}

static void print_state(const struct boot_state *s)
{
    uart_puts("journal: seq=");
    fmt_put_udec(s->seq);
    uart_puts(" active=");
    uart_puts(slot_name(s->active));
    uart_puts(" pending=");
    uart_puts(slot_name(s->pending));
    uart_puts(" attempts=");
    fmt_put_udec(s->attempts);
    uart_puts(" confirmed=");
    fmt_put_udec(s->confirmed);
    uart_puts("\r\n");
}

static void disable_all_irqs(void)
{
    for (unsigned i = 0; i < 3U; i++) {
        NVIC_ICER(i) = 0xFFFFFFFFU;
        REG32(0xE000E280U + 4U * i) = 0xFFFFFFFFU; /* ICPR: clear pending */
    }
}

static void __attribute__((noreturn)) jump_to(uint32_t vector_table)
{
    const uint32_t *v = (const uint32_t *)vector_table;
    uint32_t sp = v[0];
    uint32_t pc = v[1];

    uart_puts("jump: sp=0x");
    fmt_put_hex(sp, 8);
    uart_puts(" pc=0x");
    fmt_put_hex(pc, 8);
    uart_puts("\r\n");
    uart_flush();

    /* No interrupt may arrive between here and the image's own setup. */
    irq_disable();
    disable_all_irqs();
    REG32(0xE000ED08U) = vector_table;   /* SCB->VTOR */
    __asm volatile ("dsb\n isb\n" ::: "memory");
    /* Image entry expects a clean core state: MSP from word 0, interrupts
     * enabled at the core level (PRIMASK clear) with nothing pending, then
     * branch to word 1 in Thumb state. */
    __asm volatile (
        "msr msp, %0\n"
        "cpsie i\n"
        "bx %1\n"
        :
        : "r"(sp), "r"(pc | 1U)
        : "memory");
    __builtin_unreachable();
}

static uint32_t version_of(uint8_t slot, enum image_result r)
{
    if (r != IMAGE_OK) {
        return 0;
    }
    return image_version_packed((const struct image_header *)slot_base(slot));
}

int main(void)
{
    uart_init(115200, false);
    uart_puts("\r\nBOOT v" FW_VERSION " (phase 3)\r\n");

    /* Reset cause from the CCM marker left by the previous run. */
    uint8_t cause = BOOT_CAUSE_POWER_ON;
    uint32_t prev_slot = SLOT_NONE;
    if (BOOT_MARKER->magic == BOOT_MARKER_MAGIC) {
        cause = (BOOT_MARKER->reason == BOOT_CAUSE_APP_REQUEST) ? BOOT_CAUSE_APP_REQUEST
                                                                 : BOOT_CAUSE_RESET_RUNNING;
        prev_slot = BOOT_MARKER->slot;
    }
    BOOT_MARKER->magic = 0;
    uart_puts("cause: ");
    uart_puts(bootlog_cause_str(cause));
    if (cause != BOOT_CAUSE_POWER_ON) {
        uart_puts(" slot=");
        uart_puts(slot_name((uint8_t)prev_slot));
    }
    uart_puts("\r\n");

    struct boot_state state;
    journal_read(&state);
    print_state(&state);

    enum image_result result[2];
    result[SLOT_A] = image_validate(SLOT_A);
    result[SLOT_B] = image_validate(SLOT_B);
    print_slot_result(SLOT_A, result[SLOT_A]);
    print_slot_result(SLOT_B, result[SLOT_B]);

    uint8_t  chosen  = SLOT_NONE;
    uint8_t  reason  = BOOT_REASON_SAFE_MODE;
    uint8_t  attempt = 0;

    if (state.pending != SLOT_NONE && state.attempts >= MAX_ATTEMPTS) {
        /* Trial exhausted: the pending image never confirmed. */
        uart_puts("rollback: slot ");
        uart_puts(slot_name(state.pending));
        uart_puts(" failed to confirm after ");
        fmt_put_udec(state.attempts);
        uart_puts(" attempts\r\n");
        state.pending = SLOT_NONE;
        state.attempts = 0;
        state.confirmed = 0;
        journal_write(&state);
        if (result[state.active] == IMAGE_OK) {
            chosen = state.active;
            reason = BOOT_REASON_ROLLBACK;
        }
    } else if (state.pending != SLOT_NONE) {
        if (result[state.pending] == IMAGE_OK) {
            state.attempts++;
            state.confirmed = 0;
            journal_write(&state);      /* counted before the jump, never after */
            chosen  = state.pending;
            reason  = BOOT_REASON_PENDING_TRIAL;
            attempt = state.attempts;
        } else {
            uart_puts("pending slot invalid, dropping it\r\n");
            state.pending = SLOT_NONE;
            state.attempts = 0;
            journal_write(&state);
            if (result[state.active] == IMAGE_OK) {
                chosen = state.active;
                reason = BOOT_REASON_FALLBACK;
            }
        }
    } else {
        if (result[state.active] == IMAGE_OK) {
            chosen = state.active;
            reason = BOOT_REASON_ACTIVE;
        } else {
            uint8_t other = (state.active == SLOT_A) ? SLOT_B : SLOT_A;
            if (result[other] == IMAGE_OK) {
                chosen = other;
                reason = BOOT_REASON_FALLBACK;
            }
        }
    }

    uart_puts("decision: slot=");
    uart_puts(slot_name(chosen == SLOT_NONE ? SLOT_SAFE : chosen));
    uart_puts(" reason=");
    uart_puts(bootlog_reason_str(reason));
    if (reason == BOOT_REASON_PENDING_TRIAL) {
        uart_puts(" attempt=");
        fmt_put_udec(attempt);
        uart_putc('/');
        fmt_put_udec(MAX_ATTEMPTS);
    }
    uart_puts("\r\n");

    struct bootlog_entry e = {
        .journal_seq = state.seq,
        .slot        = chosen == SLOT_NONE ? SLOT_SAFE : chosen,
        .reason      = reason,
        .attempts    = attempt,
        .cause       = cause,
        .result_a    = (uint8_t)result[SLOT_A],
        .result_b    = (uint8_t)result[SLOT_B],
        .version     = chosen == SLOT_NONE ? 0U : version_of(chosen, result[chosen]),
    };
    if (!bootlog_append(&e)) {
        uart_puts("bootlog: append FAILED\r\n");
    }

    uint32_t target;
    if (chosen == SLOT_NONE) {
        uart_puts("no valid image in slot A or B, entering safe mode\r\n");
        target = SAFE_ORIGIN;
        const uint32_t *v = (const uint32_t *)target;
        if (v[0] < SRAM_BASE || v[0] > SRAM_END || (v[1] & 1U) == 0U ||
            v[1] < SAFE_ORIGIN || v[1] >= SAFE_ORIGIN + SAFE_SIZE) {
            uart_puts("safe-mode image missing too, halting\r\n");
            for (;;) {
                cpu_wfi();
            }
        }
    } else {
        target = slot_base(chosen) + IMAGE_HEADER_SIZE;
    }

    BOOT_MARKER->slot        = chosen == SLOT_NONE ? SLOT_SAFE : chosen;
    BOOT_MARKER->journal_seq = state.seq;
    BOOT_MARKER->reason      = 0;
    BOOT_MARKER->magic       = BOOT_MARKER_MAGIC;

    iwdg_start(WATCHDOG_MS);
    jump_to(target);
}

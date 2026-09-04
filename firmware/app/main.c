/*
 * OTA lab application, phase 3.
 *
 * Runs from slot A or slot B behind the bootloader. On top of the phase 1
 * and 2 behaviour (LED, heartbeat, CAN echo, watchdog, confirm, console) it
 * hosts the update task: CAN frames on the update IDs are queued from the
 * CAN ISR and handled in the main loop, where chunks are written into the
 * inactive slot (see update.c and ota_proto.h).
 *
 * Build variants for the rollback tests:
 *   APP_VARIANT_NOCONFIRM  never confirms; the watchdog resets it 1 s after
 *                          the confirm deadline.
 *   APP_VARIANT_HANG       disables interrupts and spins right after the
 *                          banner, so the watchdog fires 1 s later.
 */
#include <stdint.h>
#include <stdbool.h>

#include "stm32f4_regs.h"
#include "layout.h"
#include "rcc.h"
#include "gpio.h"
#include "uart.h"
#include "timer.h"
#include "can.h"
#include "iwdg.h"
#include "image.h"
#include "ota.h"
#include "update.h"
#include "console.h"
#include "fmt.h"

#define TICK_HZ             100U
#define BLINK_HZ            2U
#define HEARTBEAT_MS        1000U
#define CONFIRM_DEADLINE_MS 2000U
#define LED_PORT            GPIOD_BASE
#define LED_PIN             12U

static volatile uint32_t g_ticks;
static volatile uint32_t g_seconds;
static volatile uint32_t g_can_rx_count;
static volatile uint32_t g_can_tx_fail;
static volatile bool     g_confirmed;

static void on_tick(void)
{
    g_ticks++;
    uint32_t uptime_ms = g_ticks * (1000U / TICK_HZ);
    if (g_confirmed || uptime_ms < CONFIRM_DEADLINE_MS) {
        iwdg_kick();
    }
    if ((g_ticks % (TICK_HZ / (BLINK_HZ * 2U))) == 0U) {
        gpio_toggle(LED_PORT, LED_PIN);
    }
    if ((g_ticks % (TICK_HZ * HEARTBEAT_MS / 1000U)) == 0U) {
        g_seconds++;
    }
    update_tick_10ms();
}

static void on_can_rx(const struct can_frame *rx)
{
    if (update_enqueue(rx) || update_is_reserved_id(rx->id, rx->extended)) {
        return; /* update protocol frames, ours or another node's, are never echoed */
    }
    struct can_frame tx = *rx;
    tx.id = tx.extended ? (tx.id + 1U) & 0x1FFFFFFFU : (tx.id + 1U) & 0x7FFU;
    g_can_rx_count++;
    if (!can_send(&tx)) {
        g_can_tx_fail++;
    }
}

static void print_banner(bool can_ok)
{
    const struct ota_status *s = ota_status();
    const struct image_header *h = image_self_header();

    uart_puts("\r\n=== OTA-LAB app v");
    fmt_put_udec(h->ver_major);
    uart_putc('.');
    fmt_put_udec(h->ver_minor);
    uart_putc('.');
    fmt_put_udec(h->ver_patch);
    uart_puts(" (phase 3) ===\r\n");
    uart_puts("board: STM32F4 Discovery (Renode)\r\n");
    uart_puts("node: ");
    fmt_put_udec(update_node());
    uart_puts("\r\n");
    uart_puts("slot: ");
    uart_puts(s->running_slot == SLOT_A ? "A" : "B");
    uart_puts(s->is_pending ? " (pending, trial boot)" : s->is_active ? " (active)" : " (fallback)");
    uart_puts("\r\n");
#if defined(APP_VARIANT_NOCONFIRM)
    uart_puts("variant: noconfirm\r\n");
#elif defined(APP_VARIANT_HANG)
    uart_puts("variant: hang\r\n");
#else
    uart_puts("variant: good\r\n");
#endif
    uart_puts("can1: ");
    uart_puts(can_ok ? "ready\r\n" : "INIT FAILED\r\n");
    uart_puts("boot: ok\r\n");
}

int main(void)
{
    uart_init(115200, true);

    rcc_enable_ahb1(RCC_AHB1ENR_GPIODEN);
    gpio_set_mode(LED_PORT, LED_PIN, GPIO_MODE_OUTPUT);
    gpio_write(LED_PORT, LED_PIN, false);

    ota_init(image_self_slot());
    update_init(image_self_slot());
    bool can_ok = can_init(on_can_rx);
    print_banner(can_ok);

#if defined(APP_VARIANT_HANG)
    uart_puts("hang: spinning with interrupts disabled\r\n");
    irq_disable();
    for (;;) {
    }
#endif

    timer_init_periodic(TICK_HZ, on_tick);

    uint32_t printed = 0;
    for (;;) {
        cpu_wfi();
        update_poll();
        console_poll();
        while (printed < g_seconds) {
            printed++;
            uart_puts("HB seq=");
            fmt_put_udec(printed);
            uart_puts(" uptime_ms=");
            fmt_put_udec(printed * HEARTBEAT_MS);
            uart_puts(" can_rx=");
            fmt_put_udec(g_can_rx_count);
            uart_puts(" can_tx_fail=");
            fmt_put_udec(g_can_tx_fail);
            uart_puts("\r\n");

            if (printed == 1U) {
#if defined(APP_VARIANT_NOCONFIRM)
                uart_puts("confirm: skipped (noconfirm build)\r\n");
#else
                if (ota_confirm()) {
                    g_confirmed = true;
                }
#endif
            }
        }
    }
}

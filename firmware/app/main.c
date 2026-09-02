/*
 * OTA lab application, phase 1.
 *
 *  - TIM2 fires at 100 Hz. The ISR blinks the user LED (PD12) at 2 Hz and
 *    raises a heartbeat every second.
 *  - USART2 prints a boot banner and one heartbeat line per second.
 *  - Every CAN frame received on CAN1 is echoed back with its identifier
 *    incremented by one.
 *
 * The main loop sleeps in WFI and only wakes to print, so all real work
 * happens in interrupt handlers. Later phases add the OTA state machine here.
 */
#include <stdint.h>
#include <stdbool.h>

#include "stm32f4_regs.h"
#include "rcc.h"
#include "gpio.h"
#include "uart.h"
#include "timer.h"
#include "can.h"
#include "fmt.h"

#define TICK_HZ        100U
#define BLINK_HZ       2U
#define HEARTBEAT_MS   1000U
#define LED_PORT       GPIOD_BASE
#define LED_PIN        12U

static volatile uint32_t g_ticks;        /* 10 ms ticks since boot */
static volatile uint32_t g_seconds;      /* heartbeats due */
static volatile uint32_t g_can_rx_count;
static volatile uint32_t g_can_tx_fail;

static void on_tick(void)
{
    g_ticks++;
    if ((g_ticks % (TICK_HZ / (BLINK_HZ * 2U))) == 0U) {
        gpio_toggle(LED_PORT, LED_PIN);
    }
    if ((g_ticks % (TICK_HZ * HEARTBEAT_MS / 1000U)) == 0U) {
        g_seconds++;
    }
}

static void on_can_rx(const struct can_frame *rx)
{
    struct can_frame tx = *rx;
    if (tx.extended) {
        tx.id = (tx.id + 1U) & 0x1FFFFFFFU;
    } else {
        tx.id = (tx.id + 1U) & 0x7FFU;
    }
    g_can_rx_count++;
    if (!can_send(&tx)) {
        g_can_tx_fail++;
    }
}

static void print_banner(bool can_ok)
{
    uart_puts("\r\n");
    uart_puts("=== OTA-LAB app v" FW_VERSION " (phase 1) ===\r\n");
    uart_puts("board: STM32F4 Discovery (Renode)\r\n");
    uart_puts("can1: ");
    uart_puts(can_ok ? "ready\r\n" : "INIT FAILED\r\n");
    uart_puts("boot: ok\r\n");
}

int main(void)
{
    uart_init(115200, false);

    rcc_enable_ahb1(RCC_AHB1ENR_GPIODEN);
    gpio_set_mode(LED_PORT, LED_PIN, GPIO_MODE_OUTPUT);
    gpio_write(LED_PORT, LED_PIN, false);

    bool can_ok = can_init(on_can_rx);
    print_banner(can_ok);

    timer_init_periodic(TICK_HZ, on_tick);

    uint32_t printed = 0;
    for (;;) {
        cpu_wfi();
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
        }
    }
}

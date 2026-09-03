/*
 * Safe-mode image, sector 4, unsigned and installed together with the
 * bootloader. Runs when neither slot holds a valid image: keeps the
 * watchdog fed, prints a banner, and serves the console so the host can
 * read the boot log and state. Phase 3 adds the update receiver here.
 */
#include <stdint.h>
#include <stdbool.h>

#include "stm32f4_regs.h"
#include "layout.h"
#include "rcc.h"
#include "gpio.h"
#include "uart.h"
#include "timer.h"
#include "iwdg.h"
#include "ota.h"
#include "console.h"
#include "fmt.h"

#define TICK_HZ 100U

static volatile uint32_t g_ticks;

static void on_tick(void)
{
    g_ticks++;
    iwdg_kick();
    if ((g_ticks % 10U) == 0U) {
        gpio_toggle(GPIOD_BASE, 12); /* fast blink: 5 Hz says "safe mode" */
    }
}

int main(void)
{
    uart_init(115200, true);
    rcc_enable_ahb1(RCC_AHB1ENR_GPIODEN);
    gpio_set_mode(GPIOD_BASE, 12, GPIO_MODE_OUTPUT);

    ota_init(SLOT_SAFE);
    uart_puts("\r\n=== SAFE MODE v" FW_VERSION " ===\r\n");
    uart_puts("no valid image in slot A or B\r\n");
    ota_print_state();
    uart_puts("waiting for update\r\n");

    timer_init_periodic(TICK_HZ, on_tick);

    uint32_t printed = 0;
    for (;;) {
        cpu_wfi();
        console_poll();
        if (g_ticks / (TICK_HZ * 5U) > printed) {
            printed = g_ticks / (TICK_HZ * 5U);
            uart_puts("SAFE waiting uptime_s=");
            fmt_put_udec(printed * 5U);
            uart_puts("\r\n");
        }
    }
}

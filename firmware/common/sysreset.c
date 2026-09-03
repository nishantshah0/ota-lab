#include "sysreset.h"
#include "stm32f4_regs.h"
#include "boot_marker.h"
#include "bootlog.h"
#include "uart.h"

#define SCB_AIRCR          REG32(0xE000ED0CU)
#define AIRCR_VECTKEY      0x05FA0000U
#define AIRCR_SYSRESETREQ  (1U << 2)

void system_reset(void)
{
    uart_flush();
    irq_disable();
    if (BOOT_MARKER->magic == BOOT_MARKER_MAGIC) {
        BOOT_MARKER->reason = BOOT_CAUSE_APP_REQUEST;
    }
    __asm volatile ("dsb" ::: "memory");
    SCB_AIRCR = AIRCR_VECTKEY | AIRCR_SYSRESETREQ;
    __asm volatile ("dsb" ::: "memory");
    for (;;) {
        /* If the core reset was not honoured, the unfed watchdog fires within a second. */
    }
}

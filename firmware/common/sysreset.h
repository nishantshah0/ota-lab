#ifndef SYSRESET_H
#define SYSRESET_H

/*
 * Reboot through the bootloader. Marks the CCM boot marker with
 * BOOT_CAUSE_APP_REQUEST, requests a core reset via SCB->AIRCR, and if
 * that does not take effect stops feeding the watchdog so it does.
 */
void system_reset(void) __attribute__((noreturn));

#endif

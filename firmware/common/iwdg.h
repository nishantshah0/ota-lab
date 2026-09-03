#ifndef IWDG_H
#define IWDG_H

#include <stdint.h>

/*
 * Independent watchdog, clocked by the 32 kHz LSI. Once started it cannot
 * be stopped except by a reset, which is the point: the bootloader starts it
 * right before jumping, and an image that stops kicking it (hang, or a
 * deliberate refusal to confirm) comes back through the bootloader.
 */
void iwdg_start(uint32_t timeout_ms); /* 1 .. 4096 ms with the /32 prescaler */
void iwdg_kick(void);

#endif

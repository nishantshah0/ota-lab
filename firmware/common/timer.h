#ifndef TIMER_H
#define TIMER_H

#include <stdint.h>

/*
 * Start TIM2 as a periodic interrupt source at tick_hz. The callback runs in
 * interrupt context (TIM2_IRQHandler) and must be short.
 *
 * TIMER_CLOCK_HZ comes from the build system. Renode models every STM32
 * timer at 10 MHz regardless of RCC settings; real silicon on HSI would
 * feed TIM2 with 16 MHz. The prescaler is derived from that constant, so
 * switching targets is a one-line change in CMake.
 */
void timer_init_periodic(uint32_t tick_hz, void (*callback)(void));

#endif

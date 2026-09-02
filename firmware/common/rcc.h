#ifndef RCC_H
#define RCC_H

#include <stdint.h>

/* Enable peripheral clocks. Masks are the RCC_*ENR_* bits from stm32f4_regs.h. */
void rcc_enable_ahb1(uint32_t mask);
void rcc_enable_apb1(uint32_t mask);
void rcc_enable_apb2(uint32_t mask);

/*
 * The system runs from the 16 MHz HSI oscillator with no PLL and no bus
 * prescalers, so every bus clock is 16 MHz. Renode ignores clock tree
 * configuration entirely, and keeping HSI avoids PLL lock sequencing.
 */
#define SYSCLK_HZ  16000000U
#define APB1_HZ    SYSCLK_HZ

#endif

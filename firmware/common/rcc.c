#include "rcc.h"
#include "stm32f4_regs.h"

void rcc_enable_ahb1(uint32_t mask)
{
    RCC_AHB1ENR |= mask;
    (void)RCC_AHB1ENR; /* read back: the reference manual asks for a delay after enabling */
}

void rcc_enable_apb1(uint32_t mask)
{
    RCC_APB1ENR |= mask;
    (void)RCC_APB1ENR;
}

void rcc_enable_apb2(uint32_t mask)
{
    RCC_APB2ENR |= mask;
    (void)RCC_APB2ENR;
}

#include "timer.h"
#include "rcc.h"
#include "stm32f4_regs.h"

#ifndef TIMER_CLOCK_HZ
#error "TIMER_CLOCK_HZ must be defined by the build system"
#endif

#define TIM TIM2_BASE

static void (*tick_cb)(void);

void timer_init_periodic(uint32_t tick_hz, void (*callback)(void))
{
    tick_cb = callback;

    rcc_enable_apb1(RCC_APB1ENR_TIM2EN);

    /*
     * Two stage division: prescaler brings the timer clock down to 100 kHz,
     * then the auto reload register sets the period in 10 us units.
     * Counter counts 0..ARR inclusive, so the period is (ARR + 1) ticks.
     */
    const uint32_t counter_hz = 100000U;
    TIM_CR1(TIM)  = 0;
    TIM_PSC(TIM)  = (TIMER_CLOCK_HZ / counter_hz) - 1U;
    TIM_ARR(TIM)  = (counter_hz / tick_hz) - 1U;
    TIM_EGR(TIM)  = TIM_EGR_UG;   /* load PSC/ARR now, reset the counter */
    TIM_SR(TIM)   = 0;            /* UG also sets UIF; clear it before enabling IRQ */
    TIM_DIER(TIM) = TIM_DIER_UIE;
    nvic_enable_irq(IRQ_TIM2);
    TIM_CR1(TIM)  = TIM_CR1_CEN;
}

void TIM2_IRQHandler(void)
{
    /* UIF is rc_w0: write zero to the bit to clear it. */
    TIM_SR(TIM) &= ~TIM_SR_UIF;
    if (tick_cb != 0) {
        tick_cb();
    }
}

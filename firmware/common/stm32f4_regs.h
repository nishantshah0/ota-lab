/*
 * Register definitions for the handful of STM32F407 peripherals used here.
 * Deliberately minimal: only the registers and bits the drivers touch.
 */
#ifndef STM32F4_REGS_H
#define STM32F4_REGS_H

#include <stdint.h>

#define REG32(addr) (*(volatile uint32_t *)(addr))

/* Peripheral base addresses (RM0090, memory map). */
#define TIM2_BASE    0x40000000U
#define USART2_BASE  0x40004400U
#define CAN1_BASE    0x40006400U
#define GPIOA_BASE   0x40020000U
#define GPIOB_BASE   0x40020400U
#define GPIOD_BASE   0x40020C00U
#define RCC_BASE     0x40023800U

/* RCC */
#define RCC_AHB1ENR  REG32(RCC_BASE + 0x30U)
#define RCC_APB1ENR  REG32(RCC_BASE + 0x40U)
#define RCC_APB2ENR  REG32(RCC_BASE + 0x44U)

#define RCC_AHB1ENR_GPIOAEN  (1U << 0)
#define RCC_AHB1ENR_GPIOBEN  (1U << 1)
#define RCC_AHB1ENR_GPIODEN  (1U << 3)
#define RCC_APB1ENR_TIM2EN   (1U << 0)
#define RCC_APB1ENR_USART2EN (1U << 17)
#define RCC_APB1ENR_CAN1EN   (1U << 25)

/* GPIO (offsets from a port base) */
#define GPIO_MODER(base)   REG32((base) + 0x00U)
#define GPIO_OTYPER(base)  REG32((base) + 0x04U)
#define GPIO_OSPEEDR(base) REG32((base) + 0x08U)
#define GPIO_PUPDR(base)   REG32((base) + 0x0CU)
#define GPIO_IDR(base)     REG32((base) + 0x10U)
#define GPIO_ODR(base)     REG32((base) + 0x14U)
#define GPIO_BSRR(base)    REG32((base) + 0x18U)
#define GPIO_AFRL(base)    REG32((base) + 0x20U)
#define GPIO_AFRH(base)    REG32((base) + 0x24U)

/* USART */
#define USART_SR(base)  REG32((base) + 0x00U)
#define USART_DR(base)  REG32((base) + 0x04U)
#define USART_BRR(base) REG32((base) + 0x08U)
#define USART_CR1(base) REG32((base) + 0x0CU)

#define USART_SR_RXNE     (1U << 5)
#define USART_SR_TC       (1U << 6)
#define USART_SR_TXE      (1U << 7)
#define USART_CR1_RE      (1U << 2)
#define USART_CR1_TE      (1U << 3)
#define USART_CR1_RXNEIE  (1U << 5)
#define USART_CR1_UE      (1U << 13)

/* General purpose timer (TIM2..TIM5 layout) */
#define TIM_CR1(base)  REG32((base) + 0x00U)
#define TIM_DIER(base) REG32((base) + 0x0CU)
#define TIM_SR(base)   REG32((base) + 0x10U)
#define TIM_EGR(base)  REG32((base) + 0x14U)
#define TIM_CNT(base)  REG32((base) + 0x24U)
#define TIM_PSC(base)  REG32((base) + 0x28U)
#define TIM_ARR(base)  REG32((base) + 0x2CU)

#define TIM_CR1_CEN   (1U << 0)
#define TIM_DIER_UIE  (1U << 0)
#define TIM_SR_UIF    (1U << 0)
#define TIM_EGR_UG    (1U << 0)

/* bxCAN */
#define CAN_MCR(base)   REG32((base) + 0x000U)
#define CAN_MSR(base)   REG32((base) + 0x004U)
#define CAN_TSR(base)   REG32((base) + 0x008U)
#define CAN_RF0R(base)  REG32((base) + 0x00CU)
#define CAN_IER(base)   REG32((base) + 0x014U)
#define CAN_BTR(base)   REG32((base) + 0x01CU)
#define CAN_TI0R(base)  REG32((base) + 0x180U)
#define CAN_TDT0R(base) REG32((base) + 0x184U)
#define CAN_TDL0R(base) REG32((base) + 0x188U)
#define CAN_TDH0R(base) REG32((base) + 0x18CU)
#define CAN_RI0R(base)  REG32((base) + 0x1B0U)
#define CAN_RDT0R(base) REG32((base) + 0x1B4U)
#define CAN_RDL0R(base) REG32((base) + 0x1B8U)
#define CAN_RDH0R(base) REG32((base) + 0x1BCU)
#define CAN_FMR(base)   REG32((base) + 0x200U)
#define CAN_FM1R(base)  REG32((base) + 0x204U)
#define CAN_FS1R(base)  REG32((base) + 0x20CU)
#define CAN_FFA1R(base) REG32((base) + 0x214U)
#define CAN_FA1R(base)  REG32((base) + 0x21CU)
#define CAN_F0R1(base)  REG32((base) + 0x240U)
#define CAN_F0R2(base)  REG32((base) + 0x244U)

#define CAN_MCR_INRQ    (1U << 0)
#define CAN_MCR_SLEEP   (1U << 1)
#define CAN_MCR_NART    (1U << 4)
#define CAN_MCR_ABOM    (1U << 6)
#define CAN_MSR_INAK    (1U << 0)
#define CAN_MSR_SLAK    (1U << 1)
#define CAN_TSR_TME0    (1U << 26)
#define CAN_RF0R_FMP0   (3U << 0)
#define CAN_RF0R_RFOM0  (1U << 5)
#define CAN_IER_FMPIE0  (1U << 1)
#define CAN_TIR_TXRQ    (1U << 0)
#define CAN_IR_RTR      (1U << 1)
#define CAN_IR_IDE      (1U << 2)
#define CAN_FMR_FINIT   (1U << 0)

/* NVIC and core */
#define NVIC_ISER(n)  REG32(0xE000E100U + 4U * (n))
#define NVIC_ICER(n)  REG32(0xE000E180U + 4U * (n))

static inline void nvic_enable_irq(uint32_t irq)
{
    NVIC_ISER(irq >> 5) = 1U << (irq & 31U);
}

static inline void irq_disable(void) { __asm volatile ("cpsid i" ::: "memory"); }
static inline void irq_enable(void)  { __asm volatile ("cpsie i" ::: "memory"); }
static inline void cpu_wfi(void)     { __asm volatile ("wfi" ::: "memory"); }

/* IRQ numbers used by this project (vector position, not exception number). */
#define IRQ_CAN1_RX0  20U
#define IRQ_TIM2      28U
#define IRQ_USART2    38U

#endif /* STM32F4_REGS_H */

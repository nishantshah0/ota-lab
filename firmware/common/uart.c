#include "uart.h"
#include "gpio.h"
#include "rcc.h"
#include "stm32f4_regs.h"

#define UART      USART2_BASE
#define RX_BUF_SZ 256U   /* power of two */

static volatile uint8_t  rx_buf[RX_BUF_SZ];
static volatile uint32_t rx_head; /* written by ISR */
static volatile uint32_t rx_tail; /* read by uart_getc */
static bool rx_irq_mode;

void uart_init(uint32_t baud, bool rx_interrupt)
{
    rcc_enable_ahb1(RCC_AHB1ENR_GPIOAEN);
    rcc_enable_apb1(RCC_APB1ENR_USART2EN);

    gpio_set_af(GPIOA_BASE, 2, 7); /* USART2_TX */
    gpio_set_af(GPIOA_BASE, 3, 7); /* USART2_RX */

    /*
     * BRR holds a 12.4 fixed point divider of the bus clock (oversampling 16).
     * 16 MHz / 115200 = 138.9, so BRR = 0x08B. Renode uses this value only
     * to pace received characters; transmit is instantaneous there.
     */
    uint32_t div_x16 = (APB1_HZ * 16U + baud / 2U) / baud; /* divider * 16, rounded */
    USART_BRR(UART) = ((div_x16 / 16U) << 4) | (div_x16 & 0xFU);

    rx_irq_mode = rx_interrupt;
    uint32_t cr1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE;
    if (rx_interrupt) {
        cr1 |= USART_CR1_RXNEIE;
        nvic_enable_irq(IRQ_USART2);
    }
    USART_CR1(UART) = cr1;
}

void uart_putc(char c)
{
    while ((USART_SR(UART) & USART_SR_TXE) == 0U) {
    }
    USART_DR(UART) = (uint32_t)(uint8_t)c;
}

void uart_puts(const char *s)
{
    while (*s != '\0') {
        uart_putc(*s++);
    }
}

void uart_write(const uint8_t *buf, size_t len)
{
    for (size_t i = 0; i < len; i++) {
        uart_putc((char)buf[i]);
    }
}

void uart_flush(void)
{
    while ((USART_SR(UART) & USART_SR_TC) == 0U) {
    }
}

int uart_getc(void)
{
    if (!rx_irq_mode) {
        if (USART_SR(UART) & USART_SR_RXNE) {
            return (int)(USART_DR(UART) & 0xFFU);
        }
        return -1;
    }
    if (rx_head == rx_tail) {
        return -1;
    }
    uint8_t c = rx_buf[rx_tail & (RX_BUF_SZ - 1U)];
    rx_tail++;
    return c;
}

void USART2_IRQHandler(void)
{
    while (USART_SR(UART) & USART_SR_RXNE) {
        uint8_t c = (uint8_t)(USART_DR(UART) & 0xFFU); /* reading DR clears RXNE */
        if ((rx_head - rx_tail) < RX_BUF_SZ) {
            rx_buf[rx_head & (RX_BUF_SZ - 1U)] = c;
            rx_head++;
        }
        /* else: overflow, drop the byte */
    }
}

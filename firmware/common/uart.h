#ifndef UART_H
#define UART_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

/*
 * Console on USART2 (PA2 = TX, PA3 = RX, AF7). In Renode this is
 * sysbus.usart2, which the .resc script exposes on a TCP socket.
 *
 * rx_interrupt = true enables RXNE interrupts and a small ring buffer that
 * uart_getc() drains. Without it uart_getc() polls the data register.
 */
void uart_init(uint32_t baud, bool rx_interrupt);

void uart_putc(char c);
void uart_puts(const char *s);
void uart_write(const uint8_t *buf, size_t len);

/* Block until the last byte has left the shift register (TC). */
void uart_flush(void);

/* Returns the next received byte, or -1 if nothing is pending. */
int uart_getc(void);

#endif

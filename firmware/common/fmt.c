#include "fmt.h"
#include "uart.h"

void fmt_put_udec(uint32_t value)
{
    char buf[11];
    unsigned n = 0;
    do {
        buf[n++] = (char)('0' + (value % 10U));
        value /= 10U;
    } while (value != 0U);
    while (n > 0U) {
        uart_putc(buf[--n]);
    }
}

void fmt_put_hex(uint32_t value, unsigned digits)
{
    static const char hex[] = "0123456789ABCDEF";
    for (unsigned i = digits; i > 0U; i--) {
        uart_putc(hex[(value >> (4U * (i - 1U))) & 0xFU]);
    }
}

int fmt_hex_digit(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

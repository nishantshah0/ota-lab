#ifndef FMT_H
#define FMT_H

#include <stdint.h>

/* Tiny formatting helpers so we do not need printf or libc. */
void fmt_put_udec(uint32_t value);            /* decimal, no padding */
void fmt_put_hex(uint32_t value, unsigned digits); /* upper case, zero padded */
int  fmt_hex_digit(char c);                   /* -1 if not a hex digit */

#endif

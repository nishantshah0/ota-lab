#include "console.h"
#include "uart.h"
#include "ota.h"

#define LINE_MAX 32U

void console_poll(void)
{
    static char line[LINE_MAX];
    static unsigned len;

    for (;;) {
        int c = uart_getc();
        if (c < 0) {
            return;
        }
        if (c == '\r' || c == '\n') {
            line[len] = '\0';
            ota_console_line(line);
            len = 0;
        } else if (len < LINE_MAX - 1U) {
            line[len++] = (char)c;
        } else {
            len = 0;
        }
    }
}

#ifndef CONSOLE_H
#define CONSOLE_H

/*
 * Line assembler for the UART console. Call console_poll() from the main
 * loop; it drains uart_getc() and hands complete lines to ota_console_line().
 * Lines end with CR or LF; overlong lines are discarded.
 */
void console_poll(void);

#endif

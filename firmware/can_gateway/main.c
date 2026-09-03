/*
 * CAN to UART gateway, used by the test harness.
 *
 * This firmware runs on a second emulated STM32F4 that shares a CAN bus with
 * the device under test. It lets the host inject and observe CAN frames over
 * a plain TCP socket, which works on any OS without SocketCAN.
 *
 * Wire protocol on USART2, one command or event per line, SLCAN-like:
 *
 *   host -> gateway
 *     tIIIL[DD...]\r    send standard frame: 3 hex id digits, 1 length digit, data hex
 *     TIIIIIIIIL[DD...]\r send extended frame: 8 hex id digits
 *     V\r               print version
 *
 *   gateway -> host
 *     OK / ERR          result of the previous command
 *     tIIIL[DD...]      a standard frame arrived on the bus
 *     TIIIIIIIIL[DD...] an extended frame arrived on the bus
 */
#include <stdint.h>
#include <stdbool.h>

#include "stm32f4_regs.h"
#include "rcc.h"
#include "gpio.h"
#include "uart.h"
#include "can.h"
#include "fmt.h"
#include "timer.h"

#define LINE_MAX 64U

/*
 * Renode's CAN hub delivers frames with no bus timing, so a burst from the
 * host would land on the DUT faster than its 3-deep RX FIFO can be drained.
 * Real CAN spaces frames by the bit time on the wire: about 250 us for an
 * 8 byte standard frame at 500 kbit/s. The gateway enforces that spacing.
 */
#define CAN_FRAME_US 250U
static uint32_t last_tx_us;

static void tick_noop(void)
{
}

static bool paced_can_send(const struct can_frame *f)
{
    while ((uint32_t)(timer_micros() - last_tx_us) < CAN_FRAME_US) {
    }
    bool ok = can_send(f);
    last_tx_us = timer_micros();
    return ok;
}
#define RXQ_SZ   16U   /* power of two */

/*
 * Received frames are queued from the ISR and printed from the main loop.
 * Printing from the ISR would interleave a frame with whatever reply the
 * main loop was writing at the time.
 */
static struct can_frame  rxq[RXQ_SZ];
static volatile uint32_t rxq_head; /* ISR writes */
static volatile uint32_t rxq_tail; /* main reads */
static volatile uint32_t rxq_dropped;

static void emit_frame(const struct can_frame *f)
{
    uart_putc(f->extended ? 'T' : 't');
    fmt_put_hex(f->id, f->extended ? 8U : 3U);
    uart_putc((char)('0' + f->dlc));
    for (unsigned i = 0; i < f->dlc; i++) {
        fmt_put_hex(f->data[i], 2U);
    }
    uart_puts("\r\n");
}

static void on_can_rx(const struct can_frame *f)
{
    if ((rxq_head - rxq_tail) >= RXQ_SZ) {
        rxq_dropped++;
        return;
    }
    rxq[rxq_head & (RXQ_SZ - 1U)] = *f;
    rxq_head++;
}

static void drain_rx_queue(void)
{
    while (rxq_tail != rxq_head) {
        emit_frame(&rxq[rxq_tail & (RXQ_SZ - 1U)]);
        rxq_tail++;
    }
}

static bool parse_hex(const char *s, unsigned digits, uint32_t *out)
{
    uint32_t v = 0;
    for (unsigned i = 0; i < digits; i++) {
        int d = fmt_hex_digit(s[i]);
        if (d < 0) {
            return false;
        }
        v = (v << 4) | (uint32_t)d;
    }
    *out = v;
    return true;
}

static bool handle_send(const char *line, unsigned len, bool extended)
{
    unsigned id_digits = extended ? 8U : 3U;
    if (len < 1U + id_digits + 1U) {
        return false;
    }
    struct can_frame f = { .extended = extended };
    if (!parse_hex(line + 1, id_digits, &f.id)) {
        return false;
    }
    int dlc = fmt_hex_digit(line[1U + id_digits]);
    if (dlc < 0 || dlc > 8) {
        return false;
    }
    f.dlc = (uint8_t)dlc;
    if (len != 1U + id_digits + 1U + 2U * f.dlc) {
        return false;
    }
    for (unsigned i = 0; i < f.dlc; i++) {
        uint32_t b;
        if (!parse_hex(line + 2U + id_digits + 2U * i, 2U, &b)) {
            return false;
        }
        f.data[i] = (uint8_t)b;
    }
    return paced_can_send(&f);
}

static void handle_line(const char *line, unsigned len)
{
    if (len == 0U) {
        return;
    }
    bool ok;
    switch (line[0]) {
    case 't': ok = handle_send(line, len, false); break;
    case 'T': ok = handle_send(line, len, true);  break;
    case 'V':
        uart_puts("GW v" FW_VERSION "\r\n");
        ok = true;
        break;
    default:
        ok = false;
        break;
    }
    uart_puts(ok ? "OK\r\n" : "ERR\r\n");
}

int main(void)
{
    uart_init(115200, true);
    timer_init_periodic(100, tick_noop);
    bool can_ok = can_init(on_can_rx);

    uart_puts("\r\nGW ready can1=");
    uart_puts(can_ok ? "ok" : "fail");
    uart_puts("\r\n");

    char line[LINE_MAX];
    unsigned len = 0;
    for (;;) {
        drain_rx_queue();
        int c = uart_getc();
        if (c < 0) {
            cpu_wfi();
            continue;
        }
        if (c == '\r' || c == '\n') {
            line[len] = '\0';
            handle_line(line, len);
            len = 0;
        } else if (len < LINE_MAX - 1U) {
            line[len++] = (char)c;
        } else {
            len = 0; /* overlong line, discard */
        }
    }
}

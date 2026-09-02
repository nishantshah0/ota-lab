#ifndef CAN_H
#define CAN_H

#include <stdint.h>
#include <stdbool.h>

struct can_frame {
    uint32_t id;      /* 11-bit standard or 29-bit extended identifier */
    bool     extended;
    bool     remote;
    uint8_t  dlc;     /* 0..8 */
    uint8_t  data[8];
};

typedef void (*can_rx_callback_t)(const struct can_frame *frame);

/*
 * Bring up bxCAN1 on PB8 (RX) / PB9 (TX), AF9, at 500 kbit/s, with one
 * accept-all filter routed to FIFO 0. Frames are delivered to rx_callback
 * from CAN1_RX0_IRQHandler.
 *
 * Returns false if the controller did not acknowledge initialisation.
 */
bool can_init(can_rx_callback_t rx_callback);

/* Queue a frame in transmit mailbox 0. Returns false if the mailbox is busy. */
bool can_send(const struct can_frame *frame);

#endif

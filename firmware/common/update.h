#ifndef UPDATE_H
#define UPDATE_H

#include <stdint.h>
#include <stdbool.h>
#include "can.h"

/*
 * Device side of the CAN update protocol (see ota_proto.h). Runs in the
 * application's main loop: the CAN ISR queues frames with update_enqueue(),
 * update_poll() drains them, and update_tick_10ms() drives the inactivity
 * timeout.
 */
void update_init(uint8_t running_slot);

/* Returns true if the frame belongs to the update protocol (and was queued). */
bool update_enqueue(const struct can_frame *f);

void update_poll(void);
void update_tick_10ms(void);

/* Console helper: prints "UPDATE state=.. slot=.. next=.. total=.." */
void update_print_status(void);

#endif

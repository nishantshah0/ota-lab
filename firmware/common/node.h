#ifndef NODE_H
#define NODE_H

#include <stdint.h>

/*
 * Node identity for the CAN update protocol. Each device on a shared bus
 * needs its own set of protocol identifiers; the node id selects them
 * (see ota_proto.h).
 *
 * The id is read from the first word of the STM32 96-bit unique device ID
 * at 0x1FFF7A10. On real silicon that word is factory random, so a product
 * would map it through a provisioning table; in the lab the Renode script
 * writes the node number there, and a device with an all-zero UID (the
 * single-device tests) is node 0.
 */
uint8_t node_id(void);

#endif

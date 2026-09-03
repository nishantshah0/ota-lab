#ifndef PROGRESS_H
#define PROGRESS_H

#include <stdint.h>
#include <stdbool.h>

/*
 * Transfer progress record, one 128 KiB sector (sector 8), append-only,
 * 32 bytes per record, CRC last, erased when full. Written once per
 * received window after that window has been programmed into the slot,
 * so a record never claims chunks that are not in flash.
 *
 *   u32 seq           record number
 *   u32 header_crc    CRC-32 of the image's 512 byte header (transfer identity)
 *   u32 image_size    header + body bytes
 *   u8  slot          target slot
 *   u8  state         enum ota_state (RECEIVING, DONE) or 0xFE for aborted
 *   u16 chunks        chunks confirmed in flash (next expected seq)
 *   u8  flags         START flags (force)
 *   u8  reserved[11]  0xFF
 *   u32 crc           CRC-32 over the first 28 bytes
 */
struct progress {
    uint32_t seq;
    uint32_t header_crc;
    uint32_t image_size;
    uint8_t  slot;
    uint8_t  state;
    uint16_t chunks;
    uint8_t  flags;
};

#define PROGRESS_STATE_ABORTED 0xFEU

bool progress_read(struct progress *out);     /* false if none */
bool progress_write(struct progress *p);      /* fills p->seq */
unsigned progress_count(void);

#endif

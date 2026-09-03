#ifndef CRC32_H
#define CRC32_H

#include <stdint.h>
#include <stddef.h>

/*
 * CRC-32 (IEEE 802.3, reflected polynomial 0xEDB88320, init and final xor
 * 0xFFFFFFFF). Identical to Python's zlib.crc32, which the host tools use.
 * Bitwise implementation, no table: 1 KiB of flash saved for a few hundred
 * microseconds per kilobyte, which does not matter at boot.
 */
uint32_t crc32_update(uint32_t crc, const void *data, size_t len);
uint32_t crc32(const void *data, size_t len);

#endif

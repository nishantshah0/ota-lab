#ifndef IMAGE_H
#define IMAGE_H

#include <stdint.h>
#include <stdbool.h>

/*
 * Signed image header, 512 bytes at the start of a slot. The image body
 * (vector table first) follows immediately at slot base + 512.
 *
 *   offset size field
 *   0      4    magic          "OTA2" = 0x3241544F little endian
 *   4      2    header_version 1
 *   6      2    header_size    512
 *   8      4    image_size     body length in bytes, multiple of 4
 *   12     1    ver_major
 *   13     1    ver_minor
 *   14     1    ver_patch
 *   15     1    flags          reserved, 0
 *   16     1    target_slot    SLOT_A or SLOT_B
 *   17     3    reserved0      0
 *   20     4    body_crc32     CRC-32 over the body
 *   24     4    load_address   slot base + 512, where the body must sit
 *   28     4    reserved1      0
 *   32     64   signature      Ed25519 over bytes [0, 32) of the header
 *                              followed by SHA-512 of the body
 *   96     416  padding        0xFF
 *
 * The signature covers everything that influences the boot decision:
 * size, version, target slot and load address. The CRC is not signed on
 * its own but the body it describes is, so a CRC mismatch means either
 * corruption (caught cheaply, before the signature check) or tampering
 * (caught by the signature).
 */

#define IMAGE_MAGIC          0x3241544FU
#define IMAGE_HEADER_VERSION 1U
#define IMAGE_SIGNED_PREFIX  32U

struct image_header {
    uint32_t magic;
    uint16_t header_version;
    uint16_t header_size;
    uint32_t image_size;
    uint8_t  ver_major;
    uint8_t  ver_minor;
    uint8_t  ver_patch;
    uint8_t  flags;
    uint8_t  target_slot;
    uint8_t  reserved0[3];
    uint32_t body_crc32;
    uint32_t load_address;
    uint32_t reserved1;
    uint8_t  signature[64];
};

enum image_result {
    IMAGE_OK             = 0,
    IMAGE_ERR_MAGIC      = 1,
    IMAGE_ERR_VERSION    = 2, /* header version or size field */
    IMAGE_ERR_SIZE       = 3, /* body does not fit the slot */
    IMAGE_ERR_SLOT       = 4, /* target_slot / load_address mismatch */
    IMAGE_ERR_CRC        = 5,
    IMAGE_ERR_SIGNATURE  = 6,
    IMAGE_ERR_VECTORS    = 7, /* initial SP or reset vector out of range */
};

static inline uint32_t image_version_packed(const struct image_header *h)
{
    return ((uint32_t)h->ver_major << 16) | ((uint32_t)h->ver_minor << 8) | h->ver_patch;
}

const char *image_result_str(enum image_result r);

/*
 * Full validation of the image in a slot against the public key compiled
 * into boot_public_key[]: header fields, size, slot, CRC, Ed25519
 * signature, vectors. Used by the bootloader before jumping and by the
 * updater before marking a slot pending, so both agree by construction.
 */
enum image_result image_validate(uint8_t slot);

/* Header of the image the caller is running from (link origin - 512). */
const struct image_header *image_self_header(void);
uint8_t image_self_slot(void);

extern const uint8_t boot_public_key[32];

#endif

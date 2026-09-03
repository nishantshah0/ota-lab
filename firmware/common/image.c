#include "image.h"
#include "layout.h"
#include "crc32.h"
#include "monocypher-ed25519.h"

#define SRAM_BASE 0x20000000U
#define SRAM_END  0x20020000U

extern uint32_t __flash_origin; /* linker symbol: link origin of this image */

const char *image_result_str(enum image_result r)
{
    switch (r) {
    case IMAGE_OK:            return "OK";
    case IMAGE_ERR_MAGIC:     return "BAD_MAGIC";
    case IMAGE_ERR_VERSION:   return "BAD_HEADER";
    case IMAGE_ERR_SIZE:      return "BAD_SIZE";
    case IMAGE_ERR_SLOT:      return "WRONG_SLOT";
    case IMAGE_ERR_CRC:       return "BAD_CRC";
    case IMAGE_ERR_SIGNATURE: return "BAD_SIGNATURE";
    case IMAGE_ERR_VECTORS:   return "BAD_VECTORS";
    default:                  return "?";
    }
}

enum image_result image_validate(uint8_t slot)
{
    const uint32_t base = slot_base(slot);
    const struct image_header *h = (const struct image_header *)base;

    if (h->magic != IMAGE_MAGIC) {
        return IMAGE_ERR_MAGIC;
    }
    if (h->header_version != IMAGE_HEADER_VERSION || h->header_size != IMAGE_HEADER_SIZE) {
        return IMAGE_ERR_VERSION;
    }
    if (h->image_size == 0U || h->image_size > SLOT_SIZE - IMAGE_HEADER_SIZE || (h->image_size & 3U) != 0U) {
        return IMAGE_ERR_SIZE;
    }
    if (h->target_slot != slot || h->load_address != base + IMAGE_HEADER_SIZE) {
        return IMAGE_ERR_SLOT;
    }

    const uint8_t *body = (const uint8_t *)(base + IMAGE_HEADER_SIZE);
    if (crc32(body, h->image_size) != h->body_crc32) {
        return IMAGE_ERR_CRC;
    }

    /* Signed message: first 32 header bytes, then SHA-512 of the body. */
    uint8_t msg[IMAGE_SIGNED_PREFIX + 64U];
    const uint8_t *hb = (const uint8_t *)h;
    for (unsigned i = 0; i < IMAGE_SIGNED_PREFIX; i++) {
        msg[i] = hb[i];
    }
    crypto_sha512(msg + IMAGE_SIGNED_PREFIX, body, h->image_size);
    if (crypto_ed25519_check(h->signature, boot_public_key, msg, sizeof msg) != 0) {
        return IMAGE_ERR_SIGNATURE;
    }

    const uint32_t *vectors = (const uint32_t *)body;
    uint32_t sp = vectors[0];
    uint32_t pc = vectors[1];
    if (sp < SRAM_BASE || sp > SRAM_END || (sp & 3U) != 0U) {
        return IMAGE_ERR_VECTORS;
    }
    if ((pc & 1U) == 0U || pc < h->load_address || pc >= h->load_address + h->image_size) {
        return IMAGE_ERR_VECTORS;
    }
    return IMAGE_OK;
}

const struct image_header *image_self_header(void)
{
    uint32_t origin = (uint32_t)&__flash_origin;
    return (const struct image_header *)(origin - IMAGE_HEADER_SIZE);
}

uint8_t image_self_slot(void)
{
    return slot_from_origin((uint32_t)&__flash_origin);
}

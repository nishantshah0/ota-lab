#include "image.h"
#include "layout.h"

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

const struct image_header *image_self_header(void)
{
    uint32_t origin = (uint32_t)&__flash_origin;
    return (const struct image_header *)(origin - IMAGE_HEADER_SIZE);
}

uint8_t image_self_slot(void)
{
    return slot_from_origin((uint32_t)&__flash_origin);
}

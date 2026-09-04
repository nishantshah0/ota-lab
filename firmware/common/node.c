#include "node.h"
#include "ota_proto.h"
#include "stm32f4_regs.h"

#define UID_BASE 0x1FFF7A10U

uint8_t node_id(void)
{
    uint32_t uid0 = REG32(UID_BASE);
    uint8_t n = (uint8_t)(uid0 & 0xFFU);
    return n > OTA_MAX_NODE ? OTA_MAX_NODE : n;
}

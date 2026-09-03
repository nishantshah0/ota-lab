#include "iwdg.h"
#include "stm32f4_regs.h"

#define IWDG_BASE 0x40003000U
#define IWDG_KR   REG32(IWDG_BASE + 0x00U)
#define IWDG_PR   REG32(IWDG_BASE + 0x04U)
#define IWDG_RLR  REG32(IWDG_BASE + 0x08U)
#define IWDG_SR   REG32(IWDG_BASE + 0x0CU)

#define KEY_ACCESS 0x5555U
#define KEY_RELOAD 0xAAAAU
#define KEY_START  0xCCCCU

void iwdg_start(uint32_t timeout_ms)
{
    if (timeout_ms == 0U) {
        timeout_ms = 1U;
    }
    if (timeout_ms > 4096U) {
        timeout_ms = 4096U;
    }
    IWDG_KR  = KEY_ACCESS;
    IWDG_PR  = 3U;                 /* LSI / 32 = 1 kHz counter */
    IWDG_RLR = timeout_ms - 1U;    /* 12-bit reload */
    while (IWDG_SR & 3U) {         /* wait for PVU/RVU on real silicon */
    }
    IWDG_KR  = KEY_RELOAD;
    IWDG_KR  = KEY_START;
}

void iwdg_kick(void)
{
    IWDG_KR = KEY_RELOAD;
}

#include "flash.h"
#include "stm32f4_regs.h"

#define FLASH_R_BASE   0x40023C00U
#define FLASH_KEYR     REG32(FLASH_R_BASE + 0x04U)
#define FLASH_SR       REG32(FLASH_R_BASE + 0x0CU)
#define FLASH_CR       REG32(FLASH_R_BASE + 0x10U)

#define FLASH_KEY1     0x45670123U
#define FLASH_KEY2     0xCDEF89ABU

#define FLASH_SR_BSY      (1U << 16)
#define FLASH_SR_ERRMASK  (0xF2U)        /* PGSERR, PGPERR, PGAERR, WRPERR, OPERR */
#define FLASH_CR_PG       (1U << 0)
#define FLASH_CR_SER      (1U << 1)
#define FLASH_CR_SNB(n)   (((uint32_t)(n) & 0xFU) << 3)
#define FLASH_CR_PSIZE_32 (2U << 8)
#define FLASH_CR_STRT     (1U << 16)
#define FLASH_CR_LOCK     (1U << 31)

static void wait_not_busy(void)
{
    while (FLASH_SR & FLASH_SR_BSY) {
    }
}

static void unlock(void)
{
    if (FLASH_CR & FLASH_CR_LOCK) {
        FLASH_KEYR = FLASH_KEY1;
        FLASH_KEYR = FLASH_KEY2;
    }
}

static void lock(void)
{
    FLASH_CR |= FLASH_CR_LOCK;
}

bool flash_erase_sector(unsigned sector)
{
    if (sector > 11U) {
        return false;
    }
    wait_not_busy();
    unlock();
    FLASH_SR = FLASH_SR_ERRMASK;                     /* clear sticky errors (rc_w1) */
    FLASH_CR = FLASH_CR_PSIZE_32 | FLASH_CR_SER | FLASH_CR_SNB(sector);
    FLASH_CR |= FLASH_CR_STRT;
    wait_not_busy();
    FLASH_CR &= ~(FLASH_CR_SER | FLASH_CR_SNB(0xFU));
    bool ok = (FLASH_SR & FLASH_SR_ERRMASK) == 0U;
    lock();
    return ok;
}

bool flash_program_word(uint32_t addr, uint32_t value)
{
    if ((addr & 3U) != 0U) {
        return false;
    }
    uint32_t current = REG32(addr);
    if ((current & value) != value) {
        return false; /* would need to set a cleared bit: erase first */
    }
    wait_not_busy();
    unlock();
    FLASH_SR = FLASH_SR_ERRMASK;
    FLASH_CR = FLASH_CR_PSIZE_32 | FLASH_CR_PG;
    REG32(addr) = value;
    wait_not_busy();
    FLASH_CR &= ~FLASH_CR_PG;
    bool ok = (FLASH_SR & FLASH_SR_ERRMASK) == 0U && REG32(addr) == value;
    lock();
    return ok;
}

bool flash_program(uint32_t addr, const void *data, size_t len)
{
    if ((len & 3U) != 0U) {
        return false;
    }
    const uint8_t *p = (const uint8_t *)data;
    for (size_t i = 0; i < len; i += 4U) {
        uint32_t w = (uint32_t)p[i] | ((uint32_t)p[i + 1] << 8) |
                     ((uint32_t)p[i + 2] << 16) | ((uint32_t)p[i + 3] << 24);
        if (!flash_program_word(addr + i, w)) {
            return false;
        }
    }
    return true;
}

bool flash_is_erased(uint32_t addr, size_t len)
{
    const uint8_t *p = (const uint8_t *)addr;
    for (size_t i = 0; i < len; i++) {
        if (p[i] != 0xFFU) {
            return false;
        }
    }
    return true;
}

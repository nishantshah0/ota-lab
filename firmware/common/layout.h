/*
 * Flash layout shared by the bootloader, the images and the host tools.
 * Keep in sync with tools/otaimg.py and docs/ARCHITECTURE.md.
 *
 *   sector  start        size   use
 *   0..1    0x08000000   32K    bootloader (public key compiled in)
 *   2       0x08008000   16K    boot journal, bank 0
 *   3       0x0800C000   16K    boot journal, bank 1
 *   4       0x08010000   64K    safe-mode image (first 16K), unsigned
 *   5       0x08020000  128K    slot A: 512 byte header + image
 *   6       0x08040000  128K    slot B: 512 byte header + image
 *   7       0x08060000  128K    boot event log ring
 *   8..11   0x08080000  512K    unused
 */
#ifndef LAYOUT_H
#define LAYOUT_H

#include <stdint.h>

#define FLASH_BASE_ADDR       0x08000000U
#define FLASH_TOTAL_SIZE      0x00100000U

#define BOOT_ORIGIN           0x08000000U
#define BOOT_SIZE             0x00008000U

#define JOURNAL_BANK0_ADDR    0x08008000U
#define JOURNAL_BANK0_SECTOR  2U
#define JOURNAL_BANK1_ADDR    0x0800C000U
#define JOURNAL_BANK1_SECTOR  3U
#define JOURNAL_BANK_SIZE     0x00004000U

#define SAFE_ORIGIN           0x08010000U
#define SAFE_SECTOR           4U
#define SAFE_SIZE             0x00004000U

#define IMAGE_HEADER_SIZE     0x200U
#define SLOT_SIZE             0x00020000U
#define SLOT_A_ADDR           0x08020000U
#define SLOT_A_SECTOR         5U
#define SLOT_B_ADDR           0x08040000U
#define SLOT_B_SECTOR         6U

#define BOOTLOG_ADDR          0x08060000U
#define BOOTLOG_SECTOR        7U
#define BOOTLOG_SIZE          0x00020000U

#define SLOT_A                0U
#define SLOT_B                1U
#define SLOT_NONE             0xFFU
#define SLOT_SAFE             2U   /* only used in log entries */

static inline uint32_t slot_base(uint8_t slot)
{
    return slot == SLOT_B ? SLOT_B_ADDR : SLOT_A_ADDR;
}

static inline uint32_t slot_sector(uint8_t slot)
{
    return slot == SLOT_B ? SLOT_B_SECTOR : SLOT_A_SECTOR;
}

/* Slot an image is linked for, derived from its link origin. */
static inline uint8_t slot_from_origin(uint32_t origin)
{
    return (origin >= SLOT_B_ADDR && origin < SLOT_B_ADDR + SLOT_SIZE) ? SLOT_B : SLOT_A;
}

#endif

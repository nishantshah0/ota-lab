#ifndef BOOT_MARKER_H
#define BOOT_MARKER_H

#include <stdint.h>

/*
 * 64 bytes at the very start of CCM RAM, outside every image's .data/.bss
 * so startup code never touches it. CCM keeps its contents across any reset
 * that is not a power cycle, so the bootloader can tell "the image was
 * running and something reset the chip" from "cold power on".
 *
 * Bootloader: sets magic, slot and journal seq just before jumping.
 * Image:      may set reason to BOOT_CAUSE_APP_REQUEST before a software reset.
 * Bootloader: reads and clears the marker on the next entry.
 */
#define BOOT_MARKER_ADDR  0x10000000U
#define BOOT_MARKER_MAGIC 0x4B524D42U /* "BMRK" */

struct boot_marker {
    uint32_t magic;
    uint32_t slot;
    uint32_t journal_seq;
    uint32_t reason;
};

#define BOOT_MARKER ((volatile struct boot_marker *)BOOT_MARKER_ADDR)

#endif

#ifndef BOOTLOG_H
#define BOOTLOG_H

#include <stdint.h>
#include <stdbool.h>

/*
 * Boot event log: append-only ring of 32 byte entries in one 128 KiB
 * sector (4096 entries). When the sector is full it is erased and the log
 * starts over; unlike the journal this log is diagnostic, so losing history
 * on wrap is acceptable and documented.
 *
 * Entry layout (little endian):
 *   u32 seq          log entry number
 *   u32 journal_seq  journal seq at the time of the decision
 *   u8  slot         SLOT_A, SLOT_B or SLOT_SAFE
 *   u8  reason       enum bootlog_reason
 *   u8  attempts     attempt counter after this boot's increment (0 if n/a)
 *   u8  cause        enum bootlog_cause
 *   u8  result_a     enum image_result for slot A
 *   u8  result_b     enum image_result for slot B
 *   u8  pad[2]       0xFF
 *   u32 version      packed version of the chosen image (major<<16|minor<<8|patch)
 *   u32 reserved[2]  0xFFFFFFFF
 *   u32 crc          CRC-32 over the first 28 bytes
 */

enum bootlog_reason {
    BOOT_REASON_ACTIVE        = 0, /* booted the confirmed active slot */
    BOOT_REASON_PENDING_TRIAL = 1, /* booted the pending slot, attempt counted */
    BOOT_REASON_FALLBACK      = 2, /* preferred slot invalid, booted the other */
    BOOT_REASON_ROLLBACK      = 3, /* pending exhausted its attempts, back to active */
    BOOT_REASON_SAFE_MODE     = 4, /* no valid image, safe-mode image */
};

enum bootlog_cause {
    BOOT_CAUSE_POWER_ON      = 0, /* no marker: cold boot or power cut */
    BOOT_CAUSE_RESET_RUNNING = 1, /* marker present: reset while an image ran (watchdog or software) */
    BOOT_CAUSE_APP_REQUEST   = 2, /* marker says the image asked for a reboot */
};

struct bootlog_entry {
    uint32_t seq;
    uint32_t journal_seq;
    uint8_t  slot;
    uint8_t  reason;
    uint8_t  attempts;
    uint8_t  cause;
    uint8_t  result_a;
    uint8_t  result_b;
    uint32_t version;
};

#define BOOTLOG_ENTRY_SIZE 32U

bool     bootlog_append(struct bootlog_entry *e);   /* fills e->seq */
unsigned bootlog_count(void);                       /* entries present (valid or not) */
bool     bootlog_get(unsigned index, struct bootlog_entry *e); /* false if torn */

const char *bootlog_reason_str(uint8_t reason);
const char *bootlog_cause_str(uint8_t cause);

#endif

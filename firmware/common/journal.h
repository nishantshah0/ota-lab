#ifndef JOURNAL_H
#define JOURNAL_H

#include <stdint.h>
#include <stdbool.h>

/*
 * Boot state journal: two 16 KiB flash sectors used as append-only banks.
 *
 * Record (16 bytes, little endian):
 *   u32 seq        monotonically increasing, 1 for the first record ever
 *   u8  active     slot that last confirmed (SLOT_A / SLOT_B)
 *   u8  pending    slot under trial, or SLOT_NONE
 *   u8  attempts   unconfirmed boots of the pending slot so far
 *   u8  confirmed  1 if this record was written by an image confirming itself
 *   u32 reserved   0xFFFFFFFF (kept erased for future use)
 *   u32 crc        CRC-32 over the first 12 bytes
 *
 * Current state = the valid record with the highest seq across both banks.
 * Records are programmed one 32-bit word at a time, CRC word last, so a
 * write interrupted by a power cut leaves a record that fails its CRC and
 * is ignored; the previous record remains the current state.
 *
 * When the current bank is full the other bank is erased and the next
 * record goes there. The erase only ever destroys records older than the
 * current one, so there is no moment at which the current state exists in
 * a partially erased sector.
 */
struct boot_state {
    uint32_t seq;
    uint8_t  active;
    uint8_t  pending;
    uint8_t  attempts;
    uint8_t  confirmed;
};

#define JOURNAL_RECORD_SIZE 16U

/* Loads the current state. Returns false (and the default state, seq 0,
 * active = SLOT_A, no pending) if the journal is empty. */
bool journal_read(struct boot_state *out);

/* Appends a new record with seq = current seq + 1. The seq field of the
 * argument is ignored and updated on success. */
bool journal_write(struct boot_state *state);

/* For diagnostics: number of records (valid or torn) in the given bank. */
unsigned journal_bank_used(unsigned bank);

#endif

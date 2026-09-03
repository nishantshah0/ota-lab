#include "journal.h"
#include "layout.h"
#include "flash.h"
#include "crc32.h"

#define RECORDS_PER_BANK (JOURNAL_BANK_SIZE / JOURNAL_RECORD_SIZE)

static const uint32_t bank_addr[2]   = { JOURNAL_BANK0_ADDR, JOURNAL_BANK1_ADDR };
static const unsigned bank_sector[2] = { JOURNAL_BANK0_SECTOR, JOURNAL_BANK1_SECTOR };

static const uint8_t *record_ptr(unsigned bank, unsigned index)
{
    return (const uint8_t *)(bank_addr[bank] + index * JOURNAL_RECORD_SIZE);
}

static uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static bool record_is_erased(const uint8_t *r)
{
    for (unsigned i = 0; i < JOURNAL_RECORD_SIZE; i++) {
        if (r[i] != 0xFFU) {
            return false;
        }
    }
    return true;
}

static bool record_decode(const uint8_t *r, struct boot_state *s)
{
    if (record_is_erased(r)) {
        return false;
    }
    if (crc32(r, 12) != rd32(r + 12)) {
        return false;
    }
    s->seq       = rd32(r);
    s->active    = r[4];
    s->pending   = r[5];
    s->attempts  = r[6];
    s->confirmed = r[7];
    if (s->active > SLOT_B || (s->pending > SLOT_B && s->pending != SLOT_NONE)) {
        return false; /* CRC matched but the content is nonsense: treat as torn */
    }
    return true;
}

/* Index just past the last non-erased record in a bank (0 = bank empty). */
static unsigned bank_used(unsigned bank)
{
    unsigned used = RECORDS_PER_BANK;
    while (used > 0U && record_is_erased(record_ptr(bank, used - 1U))) {
        used--;
    }
    return used;
}

unsigned journal_bank_used(unsigned bank)
{
    return bank_used(bank & 1U);
}

/* Best valid record in a bank. Returns false if none. */
static bool bank_best(unsigned bank, struct boot_state *best)
{
    bool found = false;
    unsigned used = bank_used(bank);
    for (unsigned i = 0; i < used; i++) {
        struct boot_state s;
        if (record_decode(record_ptr(bank, i), &s) && (!found || s.seq > best->seq)) {
            *best = s;
            found = true;
        }
    }
    return found;
}

static void default_state(struct boot_state *s)
{
    s->seq = 0;
    s->active = SLOT_A;
    s->pending = SLOT_NONE;
    s->attempts = 0;
    s->confirmed = 0;
}

/* Returns the bank holding the current record, or -1 if the journal is empty. */
static int current_bank(struct boot_state *out)
{
    struct boot_state b0, b1;
    bool h0 = bank_best(0, &b0);
    bool h1 = bank_best(1, &b1);
    if (h0 && (!h1 || b0.seq >= b1.seq)) {
        *out = b0;
        return 0;
    }
    if (h1) {
        *out = b1;
        return 1;
    }
    default_state(out);
    return -1;
}

bool journal_read(struct boot_state *out)
{
    return current_bank(out) >= 0;
}

static void encode(const struct boot_state *s, uint8_t r[JOURNAL_RECORD_SIZE])
{
    r[0] = (uint8_t)(s->seq);
    r[1] = (uint8_t)(s->seq >> 8);
    r[2] = (uint8_t)(s->seq >> 16);
    r[3] = (uint8_t)(s->seq >> 24);
    r[4] = s->active;
    r[5] = s->pending;
    r[6] = s->attempts;
    r[7] = s->confirmed;
    r[8] = r[9] = r[10] = r[11] = 0xFFU;
    uint32_t c = crc32(r, 12);
    r[12] = (uint8_t)(c);
    r[13] = (uint8_t)(c >> 8);
    r[14] = (uint8_t)(c >> 16);
    r[15] = (uint8_t)(c >> 24);
}

bool journal_write(struct boot_state *state)
{
    struct boot_state cur;
    int bank = current_bank(&cur);
    uint32_t next_seq = cur.seq + 1U;

    unsigned target;
    unsigned index;
    if (bank < 0) {
        /* Empty journal: make sure bank 0 really is erased before using it. */
        target = 0;
        index = bank_used(0);
        if (index != 0U) {
            if (!flash_erase_sector(bank_sector[0])) {
                return false;
            }
            index = 0;
        }
    } else {
        target = (unsigned)bank;
        index = bank_used(target);
        if (index >= RECORDS_PER_BANK) {
            /* Current bank full: switch. The other bank holds only older
             * records (or garbage), so erasing it cannot lose current state. */
            target ^= 1U;
            if (!flash_erase_sector(bank_sector[target])) {
                return false;
            }
            index = 0;
        }
    }

    uint8_t rec[JOURNAL_RECORD_SIZE];
    state->seq = next_seq;
    encode(state, rec);
    uint32_t addr = bank_addr[target] + index * JOURNAL_RECORD_SIZE;
    if (!flash_is_erased(addr, JOURNAL_RECORD_SIZE)) {
        return false;
    }
    /* Word order matters: seq/slots first, CRC last. */
    return flash_program(addr, rec, JOURNAL_RECORD_SIZE);
}

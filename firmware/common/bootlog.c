#include "bootlog.h"
#include "layout.h"
#include "flash.h"
#include "crc32.h"

#define ENTRIES (BOOTLOG_SIZE / BOOTLOG_ENTRY_SIZE)

static const uint8_t *entry_ptr(unsigned i)
{
    return (const uint8_t *)(BOOTLOG_ADDR + i * BOOTLOG_ENTRY_SIZE);
}

static uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void wr32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

unsigned bootlog_count(void)
{
    unsigned used = ENTRIES;
    while (used > 0U && flash_is_erased((uint32_t)entry_ptr(used - 1U), BOOTLOG_ENTRY_SIZE)) {
        used--;
    }
    return used;
}

bool bootlog_get(unsigned index, struct bootlog_entry *e)
{
    if (index >= ENTRIES) {
        return false;
    }
    const uint8_t *p = entry_ptr(index);
    if (flash_is_erased((uint32_t)p, BOOTLOG_ENTRY_SIZE) || crc32(p, 28) != rd32(p + 28)) {
        return false;
    }
    e->seq         = rd32(p);
    e->journal_seq = rd32(p + 4);
    e->slot        = p[8];
    e->reason      = p[9];
    e->attempts    = p[10];
    e->cause       = p[11];
    e->result_a    = p[12];
    e->result_b    = p[13];
    e->version     = rd32(p + 16);
    return true;
}

bool bootlog_append(struct bootlog_entry *e)
{
    unsigned used = bootlog_count();
    uint32_t last_seq = 0;
    if (used > 0U) {
        struct bootlog_entry last;
        /* The last entry may be torn; walk back to the last readable one. */
        for (unsigned i = used; i > 0U; i--) {
            if (bootlog_get(i - 1U, &last)) {
                last_seq = last.seq;
                break;
            }
        }
    }
    if (used >= ENTRIES) {
        if (!flash_erase_sector(BOOTLOG_SECTOR)) {
            return false;
        }
        used = 0;
    }

    e->seq = last_seq + 1U;
    uint8_t rec[BOOTLOG_ENTRY_SIZE];
    for (unsigned i = 0; i < BOOTLOG_ENTRY_SIZE; i++) {
        rec[i] = 0xFFU;
    }
    wr32(rec, e->seq);
    wr32(rec + 4, e->journal_seq);
    rec[8]  = e->slot;
    rec[9]  = e->reason;
    rec[10] = e->attempts;
    rec[11] = e->cause;
    rec[12] = e->result_a;
    rec[13] = e->result_b;
    wr32(rec + 16, e->version);
    wr32(rec + 28, crc32(rec, 28));
    return flash_program(BOOTLOG_ADDR + used * BOOTLOG_ENTRY_SIZE, rec, BOOTLOG_ENTRY_SIZE);
}

const char *bootlog_reason_str(uint8_t reason)
{
    switch (reason) {
    case BOOT_REASON_ACTIVE:        return "ACTIVE";
    case BOOT_REASON_PENDING_TRIAL: return "PENDING_TRIAL";
    case BOOT_REASON_FALLBACK:      return "FALLBACK";
    case BOOT_REASON_ROLLBACK:      return "ROLLBACK";
    case BOOT_REASON_SAFE_MODE:     return "SAFE_MODE";
    default:                        return "?";
    }
}

const char *bootlog_cause_str(uint8_t cause)
{
    switch (cause) {
    case BOOT_CAUSE_POWER_ON:      return "POWER_ON";
    case BOOT_CAUSE_RESET_RUNNING: return "RESET_WHILE_RUNNING";
    case BOOT_CAUSE_APP_REQUEST:   return "APP_REQUEST";
    default:                       return "?";
    }
}

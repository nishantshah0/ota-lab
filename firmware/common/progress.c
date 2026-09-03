#include "progress.h"
#include "layout.h"
#include "flash.h"
#include "crc32.h"

#define RECORD_SIZE 32U
#define RECORDS     (PROGRESS_SIZE / RECORD_SIZE)

static const uint8_t *rec_ptr(unsigned i)
{
    return (const uint8_t *)(PROGRESS_ADDR + i * RECORD_SIZE);
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

unsigned progress_count(void)
{
    unsigned used = RECORDS;
    while (used > 0U && flash_is_erased((uint32_t)rec_ptr(used - 1U), RECORD_SIZE)) {
        used--;
    }
    return used;
}

static bool decode(const uint8_t *p, struct progress *out)
{
    if (flash_is_erased((uint32_t)p, RECORD_SIZE) || crc32(p, 28) != rd32(p + 28)) {
        return false;
    }
    out->seq        = rd32(p);
    out->header_crc = rd32(p + 4);
    out->image_size = rd32(p + 8);
    out->slot       = p[12];
    out->state      = p[13];
    out->chunks     = (uint16_t)(p[14] | (p[15] << 8));
    out->flags      = p[16];
    return true;
}

bool progress_read(struct progress *out)
{
    unsigned used = progress_count();
    for (unsigned i = used; i > 0U; i--) {
        if (decode(rec_ptr(i - 1U), out)) {
            return true;
        }
    }
    return false;
}

bool progress_write(struct progress *p)
{
    unsigned used = progress_count();
    struct progress last;
    uint32_t last_seq = progress_read(&last) ? last.seq : 0U;
    if (used >= RECORDS) {
        if (!flash_erase_sector(PROGRESS_SECTOR)) {
            return false;
        }
        used = 0;
    }
    p->seq = last_seq + 1U;

    uint8_t rec[RECORD_SIZE];
    for (unsigned i = 0; i < RECORD_SIZE; i++) {
        rec[i] = 0xFFU;
    }
    wr32(rec, p->seq);
    wr32(rec + 4, p->header_crc);
    wr32(rec + 8, p->image_size);
    rec[12] = p->slot;
    rec[13] = p->state;
    rec[14] = (uint8_t)p->chunks;
    rec[15] = (uint8_t)(p->chunks >> 8);
    rec[16] = p->flags;
    wr32(rec + 28, crc32(rec, 28));
    return flash_program(PROGRESS_ADDR + used * RECORD_SIZE, rec, RECORD_SIZE);
}

#ifndef OTA_H
#define OTA_H

#include <stdint.h>
#include <stdbool.h>
#include "journal.h"

/*
 * Image-side view of the boot state, plus the UART console commands that
 * expose it to the host ("state", "log", "version").
 */
struct ota_status {
    uint8_t running_slot;      /* SLOT_A, SLOT_B or SLOT_SAFE */
    bool    has_header;        /* false for the safe-mode image */
    bool    is_pending;        /* journal says this slot is under trial */
    bool    is_active;         /* journal says this slot is the confirmed one */
    bool    confirmed_now;     /* ota_confirm() wrote a record in this run */
    struct boot_state state;   /* journal state as read at init */
};

void ota_init(uint8_t running_slot);
const struct ota_status *ota_status(void);

/*
 * Mark the running slot as the confirmed active slot. Writes a journal
 * record only if the state actually changes. Returns false if the write
 * failed.
 */
bool ota_confirm(void);

void ota_print_state(void);   /* one "STATE ..." line, fresh from flash */
void ota_print_bootlog(void); /* "LOG ..." lines followed by "LOG END n=<count>" */
void ota_print_version(void);

/* Dispatch one console line. Unknown commands answer "ERR unknown command". */
void ota_console_line(const char *line);

#endif

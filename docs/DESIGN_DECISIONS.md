# Design decisions

Each entry: what the code does, why, and what the alternative was. Written
against the code as it is, including its known gaps.

## No HAL, custom startup and linker script

`firmware/common/startup.c` holds the vector table (16 core entries plus
the 82 STM32F407 interrupts, weak aliases to a spinning default) and a
`Reset_Handler` that writes VTOR, copies `.data` from its flash load
address, zeroes `.bss` and calls `main`; every peripheral is driven through
register macros in `stm32f4_regs.h`. The reason is that a bootloader is all
about what happens between reset and `main`, and a HAL hides exactly that
(vector placement, `.data` copying, clock assumptions) behind code nobody
reads. The alternative, ST's HAL with CMSIS startup, would have cost around
30 KB of flash for the same peripherals and made the linker and jump logic
harder to explain.

## `--defsym` slot parametrisation and vector table relocation

One linker script serves every image: `stm32f4.ld` defines
`__flash_origin` and `__flash_length` with `DEFINED()` fallbacks, and
`add_firmware()` in `CMakeLists.txt` passes `-Wl,--defsym` values so the
bootloader links at 0x08000000, the safe-mode image at 0x08010000 and the
application at slot base plus 0x200 for each slot. The vector table is the
first thing in each image, so the bootloader relocates by writing
`SCB->VTOR` with the slot's table address before branching, and the image's
own `Reset_Handler` writes VTOR again so it does not depend on who launched
it; 0x200 keeps the table at the 512 byte alignment the Cortex-M4 needs for
98 vectors. The alternative, position-independent code, is not practical on
Cortex-M without a runtime relocation pass, and a single fixed slot would
have meant no A/B at all.

## Ed25519 via Monocypher; where the public key lives

The bootloader verifies a 64 byte Ed25519 signature with Monocypher 4.0.2
(`image_validate()` in `image.c`), over the 32 signed header bytes followed
by SHA-512 of the body, because Monocypher's verify is one-shot and the
header and body are 512 bytes apart in flash. Ed25519 costs about 10 KB of
flash and a 32 byte key, verifies in a few million cycles, and Monocypher
has no dependencies at all; RSA-2048 would have needed a bignum library,
256 byte signatures and a slower verify, and a plain hash gives integrity
but no authenticity. The public key is the array in
`firmware/common/public_key.c`, linked into the bootloader in sectors 0 to
1 and, since phase 3, into the application for the FINISH check; the
application cannot overwrite it because the flash driver is only ever
pointed at slot sectors (`update.c` erases `slot_sector(slot)` and programs
slot addresses, and START refuses any target but A or B). That is a
software boundary, not a hardware one: no write-protection option bytes
are set, which a product would add.

## Two-bank journaled boot state

`journal.c` keeps 16 byte records (seq, active, pending, attempts,
confirmed, CRC) in sectors 2 and 3; readers take the valid record with the
highest sequence from either bank, writers append with the CRC word last,
and when the current bank is full they erase the other bank and continue
there. The point of two banks is that the erase never touches the bank
holding the current record: the window between banks, from the erase of
the other bank to the first complete record in it, still leaves the
current record intact in the old bank, so a power cut there loses
nothing. A single-sector journal, the phase 1 plan, would have had a
moment with no valid record at all during its erase.

## Watchdog timeout

The bootloader starts the IWDG at 1000 ms (`WATCHDOG_MS` in `boot/main.c`)
right before the jump; images kick it from their 100 Hz tick, and the
application stops kicking at its 2 s confirm deadline unless confirmed.
The period must exceed the longest gap between kicks: the 10 ms tick, the
few milliseconds of application init before the timer starts, and any
stretch where the tick interrupt cannot run. That last case is the known
gap: during a flash sector erase the Cortex-M4 stalls on flash reads, and
a 128 KiB sector erase on real STM32F4 silicon can take up to about two
seconds, longer than the 1 s period. Renode erases instantly, so the tests
cannot see this. On hardware the period would need to be raised to a few
seconds or the erase run from RAM with kicks around it. The alternative, no
watchdog, would leave a hung trial image running forever.

## Boot counter limit and confirm window

`MAX_ATTEMPTS` is 3 in `boot/main.c`: the bootloader increments `attempts`
in the journal before every trial jump and rolls back when the count
reaches three. `CONFIRM_DEADLINE_MS` is 2000 in `app/main.c`: the
application confirms after its first heartbeat (at 1 s) and stops feeding
the watchdog at 2 s if it has not, so an unconfirmed trial costs about 3 s
before the next attempt. Three attempts tolerate a transient failure
(brown-out, one bad boot) without giving a bad image a long time to do
damage; one attempt would roll back on a single glitch, and unlimited
attempts would never roll back a boot loop.

## Safe-mode image fallback

When neither slot validates, the bootloader jumps to an unsigned image in
sector 4 (`firmware/safe`) after checking only that its stack pointer and
reset vector are plausible; safe mode feeds the watchdog, blinks fast, and
serves the UART console. It is trusted by construction because it is
installed together with the bootloader and never updated over the air.
The alternative, halting in the bootloader with a message, was the phase 2
wording of the requirement; a separate image keeps the bootloader small and
gives the device somewhere to receive a repair. The gap: safe mode does not
run the CAN update task, so a device with two bad slots needs a programmer
today, and a fleet rollout reports it as unreachable.

## CAN chunk frame layout

A DATA frame (`ota_proto.h`) is two bytes of little-endian sequence number
plus six payload bytes. Six bytes is 75% of the 8 byte data field; on the
wire a standard-ID 8 byte frame is about 111 bits before stuffing, so the
payload efficiency is roughly 43% and a 21 KB image takes about 3600
frames. The 16 bit sequence covers 65536 x 6 = 384 KiB, three times a
slot, without any wrap logic; a one byte sequence with seven byte payloads
would have been 14% more efficient but would wrap every 1792 bytes and
make go-back-N ambiguous across a 32 chunk window.

## Transfer timeout versus slow-host detection

The device does not time out a transfer in any way that affects
correctness: the update task drops its RAM state after five minutes
without a frame (`INACTIVITY_MS` in `update.c`) purely to tidy up, and a
new START works in any state. The host owns the timing: 3 to 5 s ACK
timeouts, a retransmit on each, and it gives up only after ten consecutive
timeouts without progress. Phase 4 showed why: a 10 s device timeout fired
inside a 3 s host retry because Renode's virtual clock ran faster than the
host, and any device-side timeout tuned against host timers it cannot see
is fragile. If the device does forget, it answers NOT_STARTED and the host
re-STARTs and resumes from the progress record.

## Retransmit on NAK; out-of-order and duplicate policy

Go-back-N: the device accepts only the exact next sequence number; a
higher one gets one NAK naming the expected number (rate limited so a
burst produces a single NAK), a lower one is a duplicate and is dropped
silently; the host rewinds to the NAKed number and resends up to the next
32 chunk boundary, where the device acknowledges. Phase 4 kept this policy
after adding duplicates and reordering to the loss simulation: a swapped
pair costs one NAK and one rewind, a duplicate costs nothing. Selective
repeat would cut retransmissions under heavy loss but needs a receive
buffer larger than one window and per-chunk bookkeeping, which is not
worth it at CAN error rates.

## Monotonic version field and anti-rollback

The header carries major, minor and patch bytes packed as
`major << 16 | minor << 8 | patch`; the device compares the incoming value
with its own header's value twice, advisory at START from the claimed
version and authoritative at FINISH from the signed header now in flash
(`handle_finish()` in `update.c`). Lower is refused with VERSION_LOW
unless START carried the force flag, equal is allowed so an image can be
reinstalled. The alternative, a separate monotonic security counter in the
header, would allow a version scheme decoupled from marketing versions;
three bytes of semver were enough here and are what the signer already
had.

## Renode rather than hardware; what it cannot prove

Every test runs in Renode: it gives deterministic virtual time,
instruction-exact power cuts through watchpoints, flash snapshots through
the monitor, and a five-device fleet on one laptop with no wiring.
What it cannot prove: real timing (flash erase and program durations,
CAN bus arbitration and error frames, clock accuracy), analog behaviour,
and anything the models simplify, which this project hit three times:
STM32 timers modelled at a fixed 10 MHz, a CAN hub with no bus timing
that overflowed the receive FIFO until the gateway paced its frames, and
the 1.16.1 crash on DLC 0 frames documented in `renode_issue.md`. The
firmware is written against the reference manual, not the models, so it
should move to a Discovery board with the watchdog period revisited first.

## Fault injection method

All fault injection is emulator-side or host-side; there is no test-only
build flag in the firmware and the production image (`app_good`) carries
no hooks. Power cuts are watchpoints on flash writes with a dump and cold
restart (every `test_power_cut_*` and the random campaign in
`test_faults.py`, and the journal cut in `test_ab_boot.py`); bit rot is
applied to flash images before a boot; bus faults are options on the host
sender (`drop_rate`, `dup_rate`, `reorder_rate`, `corrupt_chunk`). The only
test-specific firmware artefacts are the `noconfirm` and `hang`
application variants, built as separate images with compile definitions,
used by the rollback, watchdog and fleet-revert tests. The alternative, a
fault-injection hook compiled into the firmware, would have tested a
different binary from the one that ships.

## Staged rollout halt policy

`tools/fleet.py` splits the fleet into cumulative percentage stages,
updates every device in a stage concurrently (INFO, transfer into the
inactive slot, REBOOT, then INFO polling), and treats a device as done
only when it reports the target slot as both running and active, which
means the new image confirmed itself. Any failure in a stage (no reply,
rejected image, a ROLLBACK in the boot log, or no confirm within the
window) halts the rollout before the next stage and names the node and
the reason; devices in the same stage that already finished are left on
the new version. The alternative, skipping the failed device and carrying
on, would spread an image that has just proven it can fail; halting is the
conservative choice for a system whose devices can recover on their own.

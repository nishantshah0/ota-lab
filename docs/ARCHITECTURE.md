# Architecture

Secure OTA firmware update lab for STM32F4, emulated in Renode, tested with pytest.
This document is updated at the end of every phase.

Current phase: **2, A/B bootloader** (built, run and tested; bring-up logs in
[PHASE1_NOTES.md](PHASE1_NOTES.md) and [PHASE2_NOTES.md](PHASE2_NOTES.md)).

## Repository layout

```
CMakeLists.txt              top-level build: add_firmware(), add_signed_image()
cmake/arm-none-eabi.cmake   cross toolchain file
firmware/common/            startup, linker script, drivers, journal, boot log, image header
firmware/common/monocypher/ vendored Monocypher 4.0.2 (Ed25519 + SHA-512)
firmware/boot/              A/B bootloader, sectors 0..1, public key compiled in
firmware/safe/              safe-mode image, sector 4
firmware/app/               application, linked for slot A and B in three variants
firmware/can_gateway/       test helper firmware: CAN <-> UART bridge
tools/                      keygen.py, sign_image.py, mkflash.py and their libraries
keys/                       development Ed25519 key pair (test only)
renode/                     platform description and lab script
tests/                      pytest suite and Renode harness
docs/                       this file, phase notes
.github/workflows/ci.yml    build + test on every push, plus a Docker job
Dockerfile                  reproducible toolchain + Renode image
```

## Target and emulation platform

The target is the STM32F407VG as found on the STM32F4 Discovery board. Renode
ships this board as `platforms/boards/stm32f4_discovery.repl`, which includes
`platforms/cpus/stm32f4.repl`. Peripherals in use and how Renode 1.16.1
models them:

| Peripheral | Address    | Renode model | Observed behaviour |
|------------|------------|--------------|--------------------|
| USART2     | 0x40004400 | UART.STM32_UART | TX instantaneous, RX paced by BRR |
| CAN1       | 0x40006400 | CAN.STMCAN | Drops frames unless a filter bank is active; DLC 0 frames crash the hub |
| TIM2       | 0x40000000 | Timers.STM32_Timer | Fixed 10 MHz input clock, RCC ignored |
| IWDG       | 0x40003000 | Timers.STM32_IndependentWatchdog | 32 kHz LSI, calls `machine.RequestReset()` on expiry |
| FLASH      | 0x40023C00 | MTD.STM32F4_FlashController | Unlock keys enforced, sector erase fills 0xFF, PG/PSIZE and error-clear bits ignored |
| GPIOD      | 0x40020C00 | GPIOPort.STM32_GPIOPort | PD12 drives `UserLED` |

Consequences:

* Clock tree configuration is not modelled, so firmware stays on the 16 MHz
  HSI. Timer prescalers derive from `TIMER_CLOCK_HZ` (10 MHz for Renode).
* After a watchdog reset Renode re-reads vectors from address 0, which the
  platform does not map. The lab script defines a `reset` macro that reloads
  the bootloader ELF (identical bytes) to re-point the core; flash content
  is preserved across the reset, as on hardware.
* `MappedMemory` accepts any write, so the flash driver enforces the
  physical "programming only clears bits" rule itself.
* Renode 1.16.1 crashes on DLC 0 CAN frames through the hub (see phase 1
  notes); that test case is skipped.

## Flash layout

STM32F407VG: 1 MiB flash at 0x08000000, 128 KiB SRAM at 0x20000000, 64 KiB
CCM RAM at 0x10000000. Flash erases per sector and the sectors are uneven,
which drives the layout:

| Sector | Start      | Size  | Use |
|--------|------------|-------|-----|
| 0..1   | 0x08000000 | 32K   | bootloader (15.1 KB used) |
| 2      | 0x08008000 | 16K   | boot journal, bank 0 |
| 3      | 0x0800C000 | 16K   | boot journal, bank 1 |
| 4      | 0x08010000 | 64K   | safe-mode image, first 16K (4.7 KB used), unsigned |
| 5      | 0x08020000 | 128K  | slot A: 512 byte signed header + image |
| 6      | 0x08040000 | 128K  | slot B: 512 byte signed header + image |
| 7      | 0x08060000 | 128K  | boot event log ring (4096 entries) |
| 8..11  | 0x08080000 | 512K  | unused |

Why this shape:

* The journal needs two erase units so that compaction never destroys the
  only copy of the current state (see "Journal atomicity"). The two 16K
  sectors right after the bootloader are ideal: small, so wear is spread
  over many records per erase, and cheap to scan.
* Slots must be equal and sector aligned so either can be erased and
  rewritten independently. Sectors 5 and 6 are the first two equal 128K
  sectors.
* The safe-mode image is part of the trusted base installed with the
  bootloader, so it is not signed and never updated over the air. It sits in
  the 64K sector because nothing else needed that odd size.
* CCM RAM keeps its content across warm resets. Its first 64 bytes hold the
  boot marker (see "Reset cause"); images link `.noinit` after it.

Images link at slot base + 0x200: the vector table must be 512 byte aligned
(98 vectors), and the header takes exactly one such block. The linker
script accepts `__flash_origin` and `__flash_length` through `--defsym`, so
one script serves the bootloader (0x08000000, 32K), the safe image
(0x08010000, 16K) and both slots (0x08020200 or 0x08040200, 0x1FE00).

Measured sizes (`-O2` for images, `-Os` for the bootloader and Monocypher):

| Image | .text | .bss | notes |
|-------|------:|-----:|-------|
| boot.elf | 15096 B | 2320 B | Ed25519 field arithmetic and SHA-512 are 70% of it |
| safe.elf | 4672 B | 2376 B | |
| app_good_A.elf | 5800 B | 2400 B | plus 512 B header once signed |

## Signed image format

```
slot base
+0x000  +------------------------------------------+
        | magic "OTA2"            u32               |  \
        | header_version u16 | header_size u16 (512)|   |
        | image_size u32 (body bytes, multiple of 4)|   |  32 bytes,
        | ver_major u8 | ver_minor | ver_patch|flags|   |  signed
        | target_slot u8 | reserved[3]              |   |  prefix
        | body_crc32 u32                            |   |
        | load_address u32 (slot base + 512)        |   |
        | reserved u32                              |  /
+0x020  | signature[64]  Ed25519(prefix || SHA-512(body))
+0x060  | 0xFF padding to 512
+0x200  +------------------------------------------+
        | image body: vector table first, then code |
        | ... image_size bytes                      |
        +------------------------------------------+
```

Validation order in the bootloader, cheapest first:

1. magic, header version and size field
2. `image_size` non-zero, multiple of 4, fits the slot
3. `target_slot` matches the slot being checked and `load_address` matches
   slot base + 512 (an image signed for A cannot be installed in B)
4. CRC-32 of the body matches `body_crc32` (fast integrity check, catches
   flash corruption and truncated writes before any crypto)
5. Ed25519 signature over `prefix || SHA-512(body)` verifies against the
   public key compiled into the bootloader
6. initial SP inside SRAM, reset vector inside the body with the Thumb bit

Result codes: `OK`, `BAD_MAGIC`, `BAD_HEADER`, `BAD_SIZE`, `WRONG_SLOT`,
`BAD_CRC`, `BAD_SIGNATURE`, `BAD_VECTORS`. Both slots are validated on every
boot and both results go into the boot log.

Why Monocypher: it is a single pair of C files, audited, constant time,
public domain / BSD-2, has no dependencies (not even libc), and its Ed25519
verify is around ten times faster than TweetNaCl on Cortex-M. The optional
`monocypher-ed25519.c` provides standard SHA-512 based Ed25519, so images
signed with Python's `cryptography` verify unchanged. Cost in the
bootloader: about 10 KB of flash and 1.3 KB of stack during verification.
TweetNaCl would have been about 4 KB smaller and roughly a second per
verification at 16 MHz, which would eat into the watchdog budget.

Why hash-then-sign: Monocypher's verify is one-shot and the header and body
are 512 bytes apart in flash. Signing over the SHA-512 of the body lets the
bootloader stream the body from flash and verify a fixed 96 byte message.

## Bootloader decision flow

```
            reset
              |
              v
   read CCM boot marker -> cause (POWER_ON / RESET_WHILE_RUNNING / APP_REQUEST), clear it
              |
              v
   journal_read() -> state {active, pending, attempts}
   (empty journal: active = A, pending = none)
              |
              v
   validate slot A, validate slot B  -> result[A], result[B]
              |
   +----------+-----------------------------+
   | pending != none                        | pending == none
   | attempts >= 3 ?                        |
   |  yes: write {pending=none,attempts=0}  |  result[active] OK ?
   |       reason = ROLLBACK                |   yes: chosen = active, ACTIVE
   |       chosen = active if OK else none  |   no : other OK ? chosen = other, FALLBACK
   |  no : result[pending] OK ?             |                 : chosen = none
   |        yes: write {attempts+1}         |
   |             chosen = pending           |
   |             reason = PENDING_TRIAL     |
   |        no : write {pending=none}       |
   |             chosen = active if OK      |
   |             reason = FALLBACK          |
   +----------+-----------------------------+
              |
              v
   append boot log entry {slot, reason, attempts, cause, result[A], result[B], version}
              |
   chosen == none ? -> target = safe-mode image (sanity check its vectors, else halt)
              |
              v
   write CCM marker {slot, journal seq}
   start IWDG (1000 ms)
   jump_to(target)
```

The attempt counter is incremented and committed to flash before the jump,
never after: a trial image that hangs or resets the chip has already been
charged for its attempt. Three unconfirmed attempts roll back.

After a rollback the rolled-back slot is not offered as a fallback: it just
failed three trials, so if the active slot is also invalid the device goes
to safe mode rather than run a known-bad image.

## The jump sequence

`jump_to(vector_table)` in `firmware/boot/main.c`:

1. Read word 0 (initial MSP) and word 1 (reset handler) from the target
   vector table. Both were range checked during validation.
2. Flush the UART so the "jump:" line is not cut off.
3. `cpsid i`: no interrupt may land between here and the image's own setup.
4. Disable and clear every NVIC interrupt (`ICER`, `ICPR`, all three words):
   the bootloader enabled none, but the marker and IWDG setup must not be
   interrupted by a stray line, and images must not inherit pending IRQs.
5. Write `SCB->VTOR` with the image's vector table address, then `dsb; isb`
   so the write is visible before any exception can vector through it.
6. `msr msp, <word 0>`: switch to the image's stack. From here on the
   bootloader's stack is gone, so nothing below this line may use it.
7. `cpsie i`: a freshly reset Cortex-M starts with PRIMASK clear; the image
   expects the same.
8. `bx <word 1 | 1>`: branch with the Thumb bit set. No return address is
   pushed; the image's `Reset_Handler` never returns.

The image's own `Reset_Handler` writes VTOR again with its own table (a
no-op here, but it keeps images independent of who launched them), copies
`.data`, zeroes `.bss`, and calls `main`. `.noinit` in CCM is not touched.

## Boot state journal

Record, 16 bytes, little endian:

```
+0  u32 seq        monotonically increasing, 1 for the first record ever
+4  u8  active     slot that last confirmed (0 = A, 1 = B)
+5  u8  pending    slot under trial, 0xFF = none
+6  u8  attempts   unconfirmed boots of the pending slot so far
+7  u8  confirmed  1 if written by an image confirming itself
+8  u32 reserved   0xFFFFFFFF
+12 u32 crc        CRC-32 over bytes 0..11
```

State transitions:

| Event | Who writes | Record |
|-------|-----------|--------|
| new image installed in slot X (phase 3, or the test harness) | installer | pending = X, attempts = 0 |
| bootloader starts a trial of pending X | bootloader, before jump | attempts + 1 |
| image X passes its self-test | image, `ota_confirm()` | active = X, pending = none, attempts = 0, confirmed = 1 |
| pending X fails validation | bootloader | pending = none |
| attempts reaches 3 | bootloader | pending = none, attempts = 0 (rollback) |

An image also confirms when it runs as a fallback (its slot was not the
active one but the active image was invalid), so the surviving image
becomes active without a manual step.

### Journal atomicity

The invariant: **at every instant, the highest-seq record with a valid CRC
is the true state, and it is never in a sector being erased.**

Three mechanisms make it hold:

1. **Append only, CRC last.** A record is programmed as four 32-bit words in
   increasing address order; the CRC is the fourth word. Flash programming
   is word atomic on STM32F4 (a word is either fully written or not). A
   power cut between words leaves a record with a wrong CRC (or none at
   all), which every reader ignores. The previous record is untouched
   because nothing is ever rewritten in place. The chance that a torn record
   accidentally carries a valid CRC over three garbage words is 2^-32 per
   event, and the content is additionally range checked.
2. **Highest seq wins.** Readers scan both banks and take the valid record
   with the largest seq. Sequence numbers only grow, so ordering does not
   depend on position, and a valid older record left behind in the other
   bank can never shadow the current one.
3. **Erase only the other bank.** When the current bank has no free slot
   the writer erases the *other* bank and appends there. The other bank
   holds only records with seq lower than the current one (or garbage), so
   an erase, or a power cut during the erase leaving half-erased records
   that fail their CRC, cannot remove the current state. The first write
   into the fresh bank is again append-with-CRC-last.

What happens on power loss at each point:

| Cut during | Flash afterwards | Next boot sees |
|------------|------------------|----------------|
| words 1..3 of a record | partial record, no CRC | previous record (torn one skipped) |
| the CRC word itself | either no CRC (torn) or a complete record | previous or new record, both consistent |
| erase of the other bank | half-erased stale records | current bank unaffected |
| first record of a fresh bank | torn record in a bank whose other records are erased | current record in the old bank, still highest seq |

Wear: 1024 records per 16K bank, and an erase only every 1024 writes.
A boot costs at most two records (attempt + confirm), so one erase per
about 500 boots per bank; at the 10k cycle rating that is five million boots
before either sector wears out. Erase of a fresh bank also happens rarely,
so the "power cut during erase" window is small; it is also harmless by
construction.

Recovery from a corrupted or empty journal is explicit: both banks empty
means "active = A, nothing pending", so a device programmed on the bench
boots slot A with no records at all and the app's `ota_confirm()` finds
nothing to write.

## Reset cause and the watchdog

* Before jumping, the bootloader writes a marker into the first 64 bytes of
  CCM RAM: magic, slot, journal seq. CCM survives every reset except power
  loss, so on the next entry the bootloader knows whether an image was
  running when the reset happened (`RESET_WHILE_RUNNING`), whether it asked
  for it (`APP_REQUEST`), or whether this is a cold start (`POWER_ON`).
  The marker is cleared immediately after reading.
* The bootloader starts the IWDG with a 1000 ms period right before the
  jump. It cannot be stopped afterwards. Images kick it from their 100 Hz
  tick. The application stops kicking at `CONFIRM_DEADLINE_MS` (2000 ms)
  unless it has confirmed, so a build that never confirms comes back to the
  bootloader at about 3 s after the jump, and a build that hangs comes back
  after 1 s. Each return is one attempt. Safe mode always kicks.

Timeline for a bad update in slot B (measured in Renode virtual time):

```
t=0.0  boot 1: pending B attempts 0 -> 1, jump B
t=1.0  B prints its first heartbeat, does not confirm
t=2.0  B stops kicking the watchdog
t=3.0  IWDG reset, cause RESET_WHILE_RUNNING slot B
       boot 2: attempts 1 -> 2 ... boot 3: attempts 2 -> 3
t~9.5  boot 4: attempts == 3 -> rollback record, jump A (reason ROLLBACK)
t~10.5 A confirms (already active: no journal write)
```

## Boot event log

32 byte append-only entries in sector 7, each CRC protected, written after
every decision and before the jump. Fields: log seq, journal seq, slot,
reason (`ACTIVE`, `PENDING_TRIAL`, `FALLBACK`, `ROLLBACK`, `SAFE_MODE`),
attempts, cause, validation result for A and for B, chosen image version.
When the sector is full it is erased and the log restarts; it is
diagnostic, so that loss is accepted.

The host reads it over the console: `log` prints one `LOG idx=.. seq=..
jseq=.. slot=.. reason=.. attempts=.. cause=.. a=.. b=.. ver=..` line per
entry and ends with `LOG END n=<count>`. `state` prints the journal state,
`version` the running image's header, `confirm` forces a confirm.

## Application and safe mode

`firmware/app`: phase 1 behaviour (LED, heartbeat, CAN echo) plus the
watchdog kick, the confirm after the first heartbeat, and the console.
Three build variants, each linked for both slots: `good`, `noconfirm`
(never confirms) and `hang` (spins with interrupts off after its banner).

`firmware/safe`: banner, 5 Hz LED, watchdog kick, console, "waiting for
update" every five seconds. Phase 3 puts the update receiver here.

## Host tools

* `tools/keygen.py` generates an Ed25519 key pair and `firmware/boot/public_key.c`.
  `keys/dev_ed25519.key` is a committed development key so that a clean
  clone builds and tests; a real product signs on a machine that holds the
  key and ships only the public half.
* `tools/sign_image.py` wraps a raw `.bin` in the header. Flags produce
  deliberately broken images for tests: `--corrupt-crc`, `--bad-signature`,
  `--wrong-slot`, `--corrupt-body`.
* `tools/mkflash.py` assembles a 512 KiB flash image from bootloader,
  safe-mode image and signed slots; `tools/flashimage.py` and
  `tools/bootjournal.py` are the libraries the tests use to craft journal
  contents and parse dumps.

## Test topology in Renode

```
 pytest (host)
   |  TCP                      TCP  |
   v                                v
 dut_term  <-- usart2 --+   +-- usart2 --> gw_term
                        |   |
                 [dut machine]   [gateway machine]
                   can1 |           | can1
                        +-- canbus -+        (Renode CAN hub)
```

The DUT machine loads the bootloader ELF (symbols, initial PC/SP) and a
complete flash image via `sysbus LoadBinary`, which stands in for a flash
programmer: each test composes its own image (which slot holds which image
variant, in what state, with what journal records). The gateway machine is
unchanged from phase 1.

Tests:

| Test | Asserts |
|------|---------|
| `test_boot_banner` | bootloader banner, `decision: slot=A reason=ACTIVE`, app banner, `boot: ok` |
| `test_heartbeat_rate` | contiguous sequence, uptime = seq x 1000 ms, count matches Renode virtual time |
| `test_led_blinks` | LED model toggles, read through the monitor |
| `test_can_echo_*`, `test_can_burst` | CAN echo with ID + 1 (DLC 0 case skipped, Renode bug) |
| `test_good_image_in_a_boots_and_confirms` | pending A boots as trial, confirms, journal ends active A |
| `test_empty_journal_defaults_to_active_a` | no journal: A active, nothing to confirm |
| `test_corrupt_crc_in_a_falls_back_to_b` | `slot A: BAD_CRC`, B boots as FALLBACK and confirms |
| `test_bad_signature_in_a_falls_back_to_b` | `slot A: BAD_SIGNATURE`, B boots |
| `test_other_invalid_images_are_rejected` | wrong slot, corrupt body, garbage |
| `test_both_slots_bad_lands_in_safe_mode` | SAFE_MODE decision, safe banner, still alive after 5 s, log entry |
| `test_never_confirming_image_rolls_back_after_three_attempts` | exactly three trials, ROLLBACK to A, causes and journal seqs |
| `test_hanging_image_triggers_watchdog_and_counts_an_attempt` | `cause: RESET_WHILE_RUNNING slot=B`, attempt 2 |
| `test_pending_image_that_fails_validation_is_dropped` | pending cleared, FALLBACK to A |
| `test_journal_ignores_torn_record` | seq 2 used, next record after the torn slot |
| `test_journal_prefers_highest_seq_across_banks` | bank 1 seq 9 beats bank 0 seq 7 |
| `test_journal_switches_bank_when_full` | 1024 records in bank 0, next write erases and uses bank 1 |
| `test_power_cut_during_journal_write_recovers_last_record` | watchpoint halt mid-record, dump, restart, torn record ignored |

## CI and reproducibility

`.github/workflows/ci.yml` installs `gcc-arm-none-eabi`, downloads the
Renode portable tarball (cached by version), installs the Python
dependencies (pytest, cryptography), builds with CMake and Ninja, runs
pytest and uploads `test-logs/`. A second job builds the `Dockerfile` and
runs the suite inside the container.

## How to run from a clean clone

Linux (matches CI):

```bash
sudo apt-get install gcc-arm-none-eabi cmake ninja-build python3-pip
wget https://github.com/renode/renode/releases/download/v1.16.1/renode-1.16.1.linux-portable.tar.gz
mkdir -p ~/renode && tar -xzf renode-1.16.1.linux-portable.tar.gz --strip-components=1 -C ~/renode
export PATH="$HOME/renode:$PATH"
pip install -r tests/requirements.txt
cmake -S . -B build -G Ninja && cmake --build build
pytest -v
```

Windows: install the Arm GNU Toolchain zip, CMake, Ninja and Python 3.12
(all available through `winget`), unpack `renode-1.16.1.windows-portable-dotnet.zip`
somewhere short, and set `RENODE` to the full path of `renode.exe`. Keep the
checkout at a short path.

Docker:

```bash
docker build -t ota-lab . && docker run --rm ota-lab
```

## Phase roadmap

| Phase | Adds |
|-------|------|
| 1 | foundation: skeleton, Renode lab, UART/CAN plumbing, harness, CI |
| 2 | A/B bootloader, signed header, journal with rollback, watchdog, safe mode, boot log |
| 3 | chunked firmware transfer over CAN into the inactive slot, flash driver reuse |
| 4 | fault injection (power cut mid-write, corrupted chunks, bad signatures) |
| 5 | fleet dashboard over many Renode instances |

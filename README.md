# OTA lab

[![CI](https://github.com/nishantshah0/ota-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/nishantshah0/ota-lab/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Target](https://img.shields.io/badge/target-STM32F407%20(Cortex--M4)-green)
![Renode](https://img.shields.io/badge/Renode-1.16.1-orange)

A secure over-the-air firmware update system for STM32F4, built and tested
without any hardware. The whole stack runs in the [Renode](https://renode.io)
emulator: an A/B bootloader with signed images and rollback, bare-metal
application images, a second emulated node acting as a CAN test fixture,
and a pytest suite that drives everything over TCP sockets. Every push
builds the firmware and runs the suite in GitHub Actions and in Docker.

The end goal is a device that receives a signed firmware image over CAN,
verifies it, installs it into the inactive slot, and rolls back on its own
if the new image fails to prove itself. Phases 1 and 2 are done; the CAN
transport is next.

## Status

| Phase | Scope | State |
|-------|-------|-------|
| 1 | Bare-metal skeleton, Renode lab, UART and CAN plumbing, test harness, CI | Done |
| 2 | A/B bootloader, Ed25519 signed images, journaled boot state with rollback, watchdog, safe mode, boot log | Done |
| 3 | Chunked firmware transfer over CAN into the inactive slot | Next |
| 4 | Fault injection: power cut mid-write, corrupted chunks, bad signatures | Planned |
| 5 | Fleet view across many emulated devices | Planned |

## What runs today

Reset lands in the bootloader (sectors 0..1). It reads the boot journal,
validates both slots (magic, size, CRC-32, Ed25519 signature over the header
and the SHA-512 of the body, vector sanity), picks a slot, records the
decision in a flash boot log, starts the independent watchdog and jumps.
A newly installed image boots as "pending"; it has to confirm itself within
two seconds or the watchdog brings the chip back, and after three
unconfirmed attempts the bootloader rolls back to the previous image. If
neither slot is valid a small safe-mode image takes over.

```
BOOT v0.2.0 (phase 2)
cause: RESET_WHILE_RUNNING slot=B
journal: seq=7 active=A pending=B attempts=2 confirmed=0
slot A: OK v0.2.0
slot B: OK v0.2.0
decision: slot=B reason=PENDING_TRIAL attempt=3/3
jump: sp=0x20020000 pc=0x080405E1

=== OTA-LAB app v0.2.0 (phase 2) ===
slot: B (pending, trial boot)
variant: noconfirm
...
```

The application itself blinks the LED from a timer interrupt, prints a
heartbeat once per second, echoes every CAN frame with the ID incremented,
and serves a console (`state`, `log`, `version`, `confirm`) so the host can
read the device's boot history. It is register-level C with its own startup
file and linker script: no HAL, no libc. Monocypher (vendored) provides
Ed25519 and SHA-512 in the bootloader.

A second emulated STM32 (`firmware/can_gateway`) sits on the same CAN bus
and bridges it to a UART with an SLCAN-style line protocol, so the host can
put frames on the bus and watch what comes back through a plain socket.

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

## Quick start

### With Docker (nothing else to install)

```bash
docker build -t ota-lab .
docker run --rm ota-lab
```

### Native

Requirements:

* Arm GNU Toolchain (`arm-none-eabi-gcc`), 10 or newer
* CMake 3.18+ and Ninja
* Renode 1.16.x, on `PATH` or pointed to by `RENODE=/path/to/renode`
* Python 3.9+ (the build signs images with the `cryptography` package)

```bash
pip install -r tests/requirements.txt
cmake -S . -B build -G Ninja
cmake --build build
pytest -v
```

The build produces the bootloader, the safe-mode image, six signed
application images (three variants for each slot) and
`build/flash/default.bin`, a complete flash image with the good app in
slot A. The test run starts a fresh headless Renode per test with a flash
image composed for that scenario and leaves `test-logs/<test>/` with the
Renode log, its stdout, both UART transcripts and the flash image used.

On Windows: the toolchain, CMake, Ninja and Python install with `winget`;
unpack the Renode Windows portable zip and set `RENODE` to `renode.exe`.
Keep the clone at a short path.

### Interactive session

```bash
renode renode/ota_lab.resc
```

Type `start` in the monitor, then connect to the UARTs with
`telnet localhost 3456` (device) and `telnet localhost 3457` (gateway). On
the device, `log` prints the boot history and `state` the journal. On the
gateway, `t1232AABB` followed by Enter sends CAN ID 0x123 with two bytes;
the device answers `t1242AABB`.

### Signing your own image

```bash
python tools/sign_image.py --key keys/dev_ed25519.key --slot B --version 0.2.1 \
    --in build/firmware/app/app_good_B.bin --out my_image_B.bin
```

`keys/dev_ed25519.key` is a development key committed so a clean clone
works; `tools/keygen.py` makes a new pair and regenerates the public key
compiled into the bootloader.

## Repository layout

```
firmware/common/      startup, linker script, drivers, journal, boot log, image header
firmware/common/monocypher/  vendored Monocypher 4.0.2
firmware/boot/        A/B bootloader
firmware/safe/        safe-mode image
firmware/app/         application, three variants x two slots
firmware/can_gateway/ CAN to UART bridge used by the tests
tools/                keygen, signer, flash image builder, journal codec
keys/                 development signing key (test only)
renode/               platform description and lab script
tests/                pytest suite and the Renode harness
docs/                 architecture, memory map, phase notes
```

## Tests

| Test | Checks |
|------|--------|
| `test_boot_banner` | bootloader banner, decision line, app banner, `boot: ok` |
| `test_heartbeat_rate` | contiguous sequence, uptime = seq x 1000 ms, count matches Renode virtual time |
| `test_led_blinks` | LED model toggles, read through the monitor |
| `test_can_echo_*`, `test_can_burst` | CAN echo with ID + 1; DLC 0 skipped (Renode bug) |
| `test_good_image_in_a_boots_and_confirms` | pending image boots as trial, confirms, journal updated |
| `test_empty_journal_defaults_to_active_a` | bench-programmed device boots A with no records |
| `test_corrupt_crc_in_a_falls_back_to_b` | BAD_CRC in A, B boots as fallback and takes over |
| `test_bad_signature_in_a_falls_back_to_b` | BAD_SIGNATURE in A, B boots |
| `test_other_invalid_images_are_rejected` | wrong slot, corrupt body, garbage |
| `test_both_slots_bad_lands_in_safe_mode` | safe-mode image runs, feeds the watchdog, serves the log |
| `test_never_confirming_image_rolls_back_after_three_attempts` | exactly three trials then rollback, causes and journal seqs on the log |
| `test_hanging_image_triggers_watchdog_and_counts_an_attempt` | watchdog reset is attributed to the running slot and counted |
| `test_pending_image_that_fails_validation_is_dropped` | pending flag cleared instead of burning attempts |
| `test_journal_ignores_torn_record` | a record cut before its CRC is skipped |
| `test_journal_prefers_highest_seq_across_banks` | bank selection by sequence number |
| `test_journal_switches_bank_when_full` | 1024 records, then the other bank is erased and used |
| `test_power_cut_during_journal_write_recovers_last_record` | core halted mid-record by a watchpoint, flash dumped, Renode killed and restarted from the dump |

Timing assertions use the emulator's virtual clock rather than the host
clock. Three consecutive local runs and both CI jobs report the same
24 passed, 1 skipped.

## Flash layout (short version)

| Sector | Start | Size | Use |
|--------|-------|------|-----|
| 0..1 | 0x08000000 | 32K | bootloader |
| 2, 3 | 0x08008000 | 2 x 16K | boot journal, two banks |
| 4 | 0x08010000 | 64K | safe-mode image |
| 5 | 0x08020000 | 128K | slot A |
| 6 | 0x08040000 | 128K | slot B |
| 7 | 0x08060000 | 128K | boot event log |

Images link at slot base + 512, after the signed header. The full story,
including the jump sequence and why the journal survives a power cut at any
point, is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Known emulator quirks

* Renode models STM32 timers at a fixed 10 MHz regardless of RCC settings.
  The firmware derives its prescaler from `TIMER_CLOCK_HZ` (a CMake
  variable) so the same code targets real silicon with one define.
* Renode's bxCAN model drops every frame until a filter bank is active.
* Renode 1.16.1 crashes when a frame with DLC 0 crosses the CAN hub. The
  test for that case is skipped with the reason recorded.
* After a watchdog reset Renode re-reads vectors from address 0, which the
  STM32F4 platform does not map. The lab script's `reset` macro re-points
  the core at the bootloader; flash content survives the reset as on
  hardware.
* The stock STM32F4 platform file references an SVD that Renode downloads
  on first use, so the first run needs network access.

More in [docs/PHASE1_NOTES.md](docs/PHASE1_NOTES.md) and
[docs/PHASE2_NOTES.md](docs/PHASE2_NOTES.md).

## License

MIT, see [LICENSE](LICENSE). Monocypher is vendored under its own licence,
see `firmware/common/monocypher/LICENCE.md`.

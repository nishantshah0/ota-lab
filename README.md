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
if the new image fails to prove itself. All five phases are done: devices
are updated end to end over CAN from a host tool, the recovery paths have
been exercised with injected power cuts, bit rot and bus faults, and a
fleet of emulated devices can be updated in stages.

## Status

| Phase | Scope | State | Where |
|-------|-------|-------|-------|
| 1 | Bare-metal skeleton, Renode lab, UART and CAN plumbing, test harness, CI | Done | [docs/PHASE1_NOTES.md](docs/PHASE1_NOTES.md), [tests/test_boot.py](tests/test_boot.py), [tests/test_can.py](tests/test_can.py) |
| 2 | A/B bootloader, Ed25519 signed images, journaled boot state with rollback, watchdog, safe mode, boot log | Done | [docs/PHASE2_NOTES.md](docs/PHASE2_NOTES.md), [tests/test_ab_boot.py](tests/test_ab_boot.py) |
| 3 | Chunked firmware delivery over CAN into the inactive slot, resume after reset, anti-rollback | Done | [docs/PHASE3_NOTES.md](docs/PHASE3_NOTES.md), [tests/test_ota_transfer.py](tests/test_ota_transfer.py) |
| 4 | Fault injection: power cuts at chosen and random flash writes, bit rot, bus faults, fuzzing | Done | [docs/FAULT_MATRIX.md](docs/FAULT_MATRIX.md), [tests/test_faults.py](tests/test_faults.py) |
| 5 | Fleet of N devices on one bus, staged rollout with halt on failure, rollout timeline | Done | [tools/fleet.py](tools/fleet.py), [tests/test_fleet.py](tests/test_fleet.py), [docs/last_rollout.svg](docs/last_rollout.svg) |

Design rationale for every major choice, including the known gaps, is in
[docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md). Every injected fault
and the test that covers it is listed in [docs/FAULT_MATRIX.md](docs/FAULT_MATRIX.md).

## What runs today

Reset lands in the bootloader (sectors 0..1). It reads the boot journal,
validates both slots (magic, size, CRC-32, Ed25519 signature over the header
and the SHA-512 of the body, vector sanity), picks a slot, records the
decision in a flash boot log, starts the independent watchdog and jumps.
A newly installed image boots as "pending"; it has to confirm itself within
two seconds or the watchdog brings the chip back, and after three
unconfirmed attempts the bootloader rolls back to the previous image. If
neither slot is valid a small safe-mode image takes over.

The application receives new images over CAN: `tools/ota_send.py` streams
a signed image in 6 byte chunks with a 32 chunk window, NAK and
retransmit, the device writes it into the inactive slot, keeps a progress
record in flash so a reset mid-transfer resumes from the last window,
validates CRC and signature on FINISH, refuses versions lower than the
running one unless forced, and only then marks the slot pending. The frame
formats, the transfer sequence, resume after reset and the anti-rollback
rule are specified in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#firmware-delivery-over-can).

```
BOOT v0.3.0 (phase 3)
cause: RESET_WHILE_RUNNING slot=B
journal: seq=7 active=A pending=B attempts=2 confirmed=0
slot A: OK v0.3.0
slot B: OK v0.3.0
decision: slot=B reason=PENDING_TRIAL attempt=3/3
jump: sp=0x20020000 pc=0x080405E1

=== OTA-LAB app v0.3.0 (phase 3) ===
slot: B (pending, trial boot)
variant: noconfirm
...
```

The application itself blinks the LED from a timer interrupt, prints a
heartbeat once per second, echoes every CAN frame with the ID incremented,
and serves a console (`state`, `log`, `version`, `confirm`, `update`, `reboot`) so the host can
read the device's boot history. It is register-level C with its own startup
file and linker script: no HAL, no libc. Monocypher (vendored) provides
Ed25519 and SHA-512 in the bootloader.

Several devices can share the bus: `tools/fleet.py` drives a staged
rollout (20%, 50%, 100% by default), waits for each device to boot and
confirm, halts on the first failure, and draws the result on Renode's
virtual clock. The image below is the one produced by
`tests/test_fleet.py::test_staged_rollout_happy_path`.

![Rollout timeline](docs/last_rollout.svg)

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
make test
```

`make test` configures, builds, and runs every test file (core, fault
injection, fleet). The last line of a passing run, measured on this
repository at the commit that introduced the fleet:

```
50 passed, 1 skipped in 1247.32s (0:20:47)
```

`make test-core`, `make test-faults` and `make test-fleet` run the three
groups separately; CI runs them as separate jobs. Under Docker the same
targets work: `docker run --rm ota-lab make test`.

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

### Device console

Commands on the device UART (port 3456 in the interactive session, or the
`send_command` helper in the test harness):

| Command | Prints |
|---------|--------|
| `state` | journal state: seq, active slot, pending slot, attempts, confirmed, bank usage |
| `log` | every boot log entry (slot, reason, attempts, reset cause, validation results, version) |
| `version` | running slot and the version and size from its signed header |
| `update` | transfer state, next chunk, total, receive counters, last progress record |
| `confirm` | mark the running slot active (the app does this itself after its first heartbeat) |
| `reboot` | software reset through the bootloader |

### Sending an update

With the interactive session running:

```bash
python tools/ota_send.py --port 3457 --image build/firmware/app/app_good_B.signed.bin
```

then `reboot` on the device console; the bootloader runs the new image as a
trial and it confirms itself.

### Signing your own image

```bash
python tools/sign_image.py --key keys/dev_ed25519.key --slot B --version 0.3.1 \
    --in build/firmware/app/app_good_B.bin --out my_image_B.bin
```

`keys/dev_ed25519.key` is a throwaway development key, committed on
purpose so that a clean clone builds signed images and the tests run
reproducibly. It protects nothing and must not be used for anything real.
[keys/README.md](keys/README.md) explains how to replace it with a key kept
off the repository; the bootloader accepts images from exactly one public
key, compiled into its own flash sectors, which no image ever writes.

### Fleet

```bash
python tools/fleet.py resc --nodes 5 --out build/fleet/fleet.resc   # generate the script
renode build/fleet/fleet.resc                                        # then: start
python tools/fleet.py status --nodes 5
python tools/fleet.py rollout --nodes 5 --image build/firmware/app/app_good_B.signed.bin \
    --stage 20,50,100 --confirm-window 10 --svg docs/last_rollout.svg
python tools/fleet.py logs --node 3
```

Each device derives its CAN identifiers from a node id the script writes
into the STM32 unique-ID area; node n uses 0x710 + 0x10 n and the two ids
above it, up to 15 nodes. A rollout that halts prints which node failed and
why, leaves earlier stages on the new version, and touches nothing beyond
the failed stage.

## Repository layout

```
firmware/common/      startup, linker script, drivers, journal, boot log, image header, update task
firmware/common/monocypher/  vendored Monocypher 4.0.2
firmware/boot/        A/B bootloader
firmware/safe/        safe-mode image
firmware/app/         application, three variants x two slots
firmware/can_gateway/ CAN to UART bridge used by the tests
tools/                keygen, signer, flash image builder, journal codec, CAN update sender, fleet tool
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
| `test_full_transfer_into_b_then_boots_b_and_confirms` | image streamed over CAN into B, reboot, trial boot, confirm; throughput recorded |
| `test_transfer_survives_random_frame_loss` | 5% of frames dropped, recovered by NAK and retransmit |
| `test_reset_mid_transfer_resumes_from_last_good_chunk` | reboot after 320 chunks, transfer resumes at 320 |
| `test_corrupted_chunk_is_caught_on_finish` | flipped bit caught by the CRC, slot not marked pending |
| `test_bad_signature_is_rejected_on_finish` | bad signature refused |
| `test_lower_version_is_rejected_unless_forced` | anti-rollback, with force override |
| `test_forged_version_in_start_does_not_bypass_anti_rollback` | the signed header is the authority |
| `test_cannot_target_the_running_slot` | SLOT_BUSY |
| `test_never_confirming_update_rolls_back_to_a` | transfer, reboot, three trials, rollback, end to end |
| `test_power_cut_during_*` (4 tests) | watchpoint power cuts on chunk, progress record, pending and confirm writes; resume or re-trial |
| `test_random_power_cut_campaign` | seeded random cuts with restarts and flash invariants, then completion |
| `test_bit_rot_in_*` (4 tests) | slot body, signature, journal record, boot log entry |
| `test_bus_duplicates_reordering_and_loss` | duplicates, swaps and loss at once |
| `test_garbage_and_malformed_frames_do_not_break_the_device` | fuzz and malformed control sequences |
| `test_staged_rollout_happy_path` | five devices, 20/50/100% stages, all confirm, timeline on virtual time |
| `test_rollout_halts_on_corrupted_device` | one device in safe mode: halt at its stage, earlier stage updated, later untouched |
| `test_rollout_halts_when_a_device_never_confirms` | one device reverts after three trials, rollout names it |

Timing assertions use the emulator's virtual clock rather than the host
clock. The skipped test is the Renode DLC 0 crash documented in
[docs/renode_issue.md](docs/renode_issue.md).

## Size

Counted with cloc 2.10 on this tree. Vendored code is Monocypher 4.0.2
(`firmware/common/monocypher/`), under its own licence; everything else in
`firmware/` is written for this project.

| Category | Files | Code lines | What |
|----------|------:|-----------:|------|
| Firmware, own C and headers | 47 | 2829 | bootloader, safe mode, application, gateway, drivers, journal, update task |
| Firmware, linker script | 1 | 94 | `stm32f4.ld` |
| Vendored C (Monocypher) | 4 | 2416 | Ed25519, SHA-512 |
| Python | 18 | 2576 | host tools and the pytest suite |
| Config | 11 | 284 | CMake, workflow YAML, Dockerfile, Makefile, pytest.ini |
| Docs | 10 | 1457 | Markdown under docs/, this file, keys/README.md |

Built image sizes are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#flash-layout).

## Flash layout (short version)

| Sector | Start | Size | Use |
|--------|-------|------|-----|
| 0..1 | 0x08000000 | 32K | bootloader |
| 2, 3 | 0x08008000 | 2 x 16K | boot journal, two banks |
| 4 | 0x08010000 | 64K | safe-mode image |
| 5 | 0x08020000 | 128K | slot A |
| 6 | 0x08040000 | 128K | slot B |
| 7 | 0x08060000 | 128K | boot event log |
| 8 | 0x08080000 | 128K | update progress records |

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

* A watchpoint (`sysbus AddWatchpointHook`) halts the core before the
  watched write happens, which is exactly a power cut at that flash
  operation; the tests dump flash through the monitor and restart from it.
* Renode's CAN hub has no bus timing, so the gateway paces its frames to
  the 250 microsecond bus time of a 500 kbit/s frame; without that the
  DUT's three-deep receive FIFO overflows on every burst.

More in [docs/PHASE1_NOTES.md](docs/PHASE1_NOTES.md),
[docs/PHASE2_NOTES.md](docs/PHASE2_NOTES.md),
[docs/PHASE3_NOTES.md](docs/PHASE3_NOTES.md) and
[docs/PHASE4_NOTES.md](docs/PHASE4_NOTES.md).

## License

MIT, see [LICENSE](LICENSE). Monocypher is vendored under its own licence,
see `firmware/common/monocypher/LICENCE.md`.

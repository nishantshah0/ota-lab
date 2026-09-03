# OTA lab

[![CI](https://github.com/nishantshah0/ota-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/nishantshah0/ota-lab/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Target](https://img.shields.io/badge/target-STM32F407%20(Cortex--M4)-green)
![Renode](https://img.shields.io/badge/Renode-1.16.1-orange)

A secure over-the-air firmware update system for STM32F4, built and tested
without any hardware. The whole stack runs in the [Renode](https://renode.io)
emulator: bare-metal firmware, a second emulated node acting as a CAN test
fixture, and a pytest suite that drives both over TCP sockets. Every push
builds the firmware and runs the suite in GitHub Actions and in Docker.

The end goal is a device that can receive a signed firmware image over CAN,
verify it, install it into an inactive slot, and roll back if the new image
fails to boot. This repository grows toward that in phases; the current state
is the phase 1 foundation.

## Status

| Phase | Scope | State |
|-------|-------|-------|
| 1 | Bare-metal skeleton, Renode lab, UART and CAN plumbing, test harness, CI | Done |
| 2 | A/B bootloader, signed image header, rollback, boot state sector | Next |
| 3 | Chunked firmware transfer over CAN, flash driver | Planned |
| 4 | Fault injection: power cut mid-write, corrupted chunks, bad signatures | Planned |
| 5 | Fleet view across many emulated devices | Planned |

## What runs today

The device under test (`firmware/app`) boots, prints a banner on USART2,
blinks the user LED from a timer interrupt, prints a heartbeat once per
second, and echoes every CAN frame it receives with the identifier
incremented by one. It is written against the registers directly: no HAL,
no libc, its own startup file and linker script.

```
=== OTA-LAB app v0.1.0 (phase 1) ===
board: STM32F4 Discovery (Renode)
can1: ready
boot: ok
HB seq=1 uptime_ms=1000 can_rx=0 can_tx_fail=0
HB seq=2 uptime_ms=2000 can_rx=1 can_tx_fail=0
```

A second emulated STM32 (`firmware/can_gateway`) sits on the same CAN bus and
bridges it to a UART with an SLCAN-style line protocol, so the host can put
frames on the bus and watch what comes back through a plain socket.

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
* Python 3.9+

```bash
pip install -r tests/requirements.txt
cmake -S . -B build -G Ninja
cmake --build build
pytest -v
```

The build prints section sizes for both images. The test run starts a fresh
headless Renode per test and leaves `test-logs/<test>/` with the Renode log,
its stdout, and both UART transcripts.

On Windows: the toolchain, CMake, Ninja and Python install with `winget`;
unpack the Renode Windows portable zip and set `RENODE` to `renode.exe`.
Keep the clone at a short path, deep directories hit the 260 character limit.

### Interactive session

```bash
renode renode/ota_lab.resc
```

Type `start` in the monitor, then connect to the UARTs with
`telnet localhost 3456` (device) and `telnet localhost 3457` (gateway). On
the gateway, `t1232AABB` followed by Enter sends CAN ID 0x123 with two bytes;
the device answers `t1242AABB`.

## Repository layout

```
firmware/common/     startup.c, stm32f4.ld, register definitions, drivers
firmware/app/        device under test
firmware/can_gateway/ CAN to UART bridge used by the tests
renode/              platform description and lab script
tests/               pytest suite and the Renode harness
docs/                architecture, memory map, bring-up notes
cmake/               arm-none-eabi toolchain file
.github/workflows/   CI: native Ubuntu job plus a Docker job
Dockerfile           toolchain + Renode + pytest in one image
```

## Tests

| Test | Checks |
|------|--------|
| `test_boot_banner` | banner, `can1: ready`, `boot: ok` on the device UART |
| `test_heartbeat_rate` | contiguous sequence, uptime = seq x 1000 ms, count matches Renode virtual time |
| `test_led_blinks` | the LED model toggles, read through the Renode monitor |
| `test_can_echo_increments_id` | ID 0x123 comes back as 0x124 with the same payload |
| `test_can_echo_edge_cases` | ID 0, ID 0x7FE, ID 0x7FF wrap; DLC 0 skipped (Renode bug, see below) |
| `test_can_echo_extended_id` | 29-bit identifiers |
| `test_can_burst` | five frames back to back |

Timing assertions use the emulator's virtual clock rather than the host
clock, so results do not depend on machine speed. Three consecutive local
runs and both CI jobs report the same 9 passed, 1 skipped.

## Memory map (short version)

STM32F407VG: 1 MiB flash at 0x08000000, 128 KiB SRAM at 0x20000000, 64 KiB
CCM RAM at 0x10000000. Flash sectors are uneven (4 x 16K, 1 x 64K, 7 x 128K),
which drives the planned partitioning:

| Sector | Start | Use |
|--------|-------|-----|
| 0..3 | 0x08000000 | bootloader |
| 4 | 0x08010000 | boot state |
| 5 | 0x08020000 | slot A |
| 6 | 0x08040000 | slot B |

Phase 1 links the app at the start of flash. The linker script accepts
`__flash_origin` and `__flash_length` via `--defsym`, so relocating into a
slot is a link flag, not a script edit. Full detail, section by section, in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Known emulator quirks

* Renode models STM32 timers at a fixed 10 MHz regardless of RCC settings.
  The firmware derives its prescaler from `TIMER_CLOCK_HZ` (a CMake
  variable) so the same code targets real silicon with one define.
* Renode's bxCAN model drops every frame until a filter bank is active.
* Renode 1.16.1 crashes when a frame with DLC 0 crosses the CAN hub
  (`NullReferenceException` in `CANMessageFrame.ToSocketCAN`). The test for
  that case is skipped with the reason recorded; nothing on this bus may
  send an empty frame until it is fixed upstream.
* The stock STM32F4 platform file references an SVD that Renode downloads
  on first use, so the first run needs network access.

More in [docs/PHASE1_NOTES.md](docs/PHASE1_NOTES.md).

## License

MIT, see [LICENSE](LICENSE).

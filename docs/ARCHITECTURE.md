# Architecture

Secure OTA firmware update lab for STM32F4, emulated in Renode, tested with pytest.
This document is updated at the end of every phase.

Current phase: **1, foundation** (built, run and tested; see
[PHASE1_NOTES.md](PHASE1_NOTES.md) for the bring-up log).

## Repository layout

```
CMakeLists.txt              top-level build, defines add_firmware()
cmake/arm-none-eabi.cmake   cross toolchain file
firmware/common/            startup, linker script, register defs, drivers
firmware/app/               device under test: blink, banner, heartbeat, CAN echo
firmware/can_gateway/       test helper firmware: CAN <-> UART bridge
renode/stm32f4_ota.repl     platform (STM32F4 Discovery plus CCM RAM)
renode/ota_lab.resc         two machines on one CAN hub, UARTs on TCP sockets
tests/                      pytest suite and Renode harness
.github/workflows/ci.yml    build + test on every push, plus a Docker job
Dockerfile                  reproducible toolchain + Renode image
```

## Target and emulation platform

The target is the STM32F407VG as found on the STM32F4 Discovery board. Renode
ships this board as `platforms/boards/stm32f4_discovery.repl`, which includes
`platforms/cpus/stm32f4.repl`. The peripherals used in phase 1 and how Renode
1.16.1 models them:

| Peripheral | Address    | IRQ        | Renode model        | Observed behaviour |
|------------|------------|------------|---------------------|-------|
| USART2     | 0x40004400 | 38         | UART.STM32_UART     | TX is instantaneous, RX paced by BRR |
| CAN1       | 0x40006400 | 19..22     | CAN.STMCAN          | Drops frames unless a filter bank is active; DLC 0 frames crash the hub (see below) |
| TIM2       | 0x40000000 | 28         | Timers.STM32_Timer  | Fixed 10 MHz input clock, RCC ignored; reports divider 100 and limit 999 with our settings |
| GPIOD      | 0x40020C00 | via EXTI   | GPIOPort.STM32_GPIOPort | PD12 drives `UserLED`, readable from the monitor |
| RCC        | 0x40023800 |            | Miscellaneous.STM32F4_RCC | Ready flags mirror enable flags |

Consequences for the firmware:

* Clock tree configuration is not modelled, so the firmware stays on the
  16 MHz HSI and never touches the PLL. Timer prescalers are derived from
  `TIMER_CLOCK_HZ` (10 MHz for Renode) set in CMake, so the same code can be
  pointed at real silicon (16 MHz) with one define. Measured in Renode: one
  heartbeat per 1.000 s of virtual time, and the firmware's own uptime counter
  agrees with the emulator clock.
* bxCAN filter bank 0 is configured as a 32-bit mask filter with mask 0 so
  every frame is accepted. Without this Renode silently discards frames.
* Renode 1.16.1 crashes (unhandled `NullReferenceException` in
  `CANMessageFrame.ToSocketCAN`, called from `CANHub.Transmit`) when a DLC 0
  frame from STMCAN crosses the hub. The firmware is not changed for this;
  the corresponding test case is skipped with that reason, and the harness
  detects the dead process immediately.
* The Renode `stm32f4.repl` maps 2 MiB of flash and 256 KiB of SRAM. The
  linker script uses the real STM32F407VG sizes (1 MiB, 128 KiB) so images
  remain valid on hardware.
* Renode downloads the STM32F40x SVD referenced by the stock platform file
  from Antmicro's server on first use and caches it afterwards, so the first
  run needs network access.

## Memory map and linker script

Physical map of the STM32F407VG:

| Region        | Start      | Size    | Use |
|---------------|------------|---------|-----|
| Main flash    | 0x08000000 | 1 MiB   | code, constants, .data load image |
| System ROM    | 0x1FFF0000 | 30 KiB  | ST bootloader, read only |
| SRAM          | 0x20000000 | 128 KiB | .data, .bss, stack |
| CCM RAM       | 0x10000000 | 64 KiB  | CPU only, no DMA; reserved for `.noinit` |

Flash is erased per sector and the sectors are not uniform, which drives the
partition plan for the A/B bootloader:

| Sector | Start      | Size    | Planned use |
|--------|------------|---------|-------------|
| 0..3   | 0x08000000 | 4 x 16K | bootloader |
| 4      | 0x08010000 | 64K     | boot state (active slot, trial counters, rollback flags) |
| 5      | 0x08020000 | 128K    | slot A |
| 6      | 0x08040000 | 128K    | slot B |
| 7..11  | 0x08060000 | 5 x 128K| spare |

Phase 1 has no bootloader, so the application links at 0x08000000. The
linker script (`firmware/common/stm32f4.ld`) takes `__flash_origin` and
`__flash_length` from `--defsym` if given, which is how the bootloader phase
will relink the same application into slot A or B without editing the script.

Output sections, in flash order:

1. `.isr_vector`: the vector table, forced first with `KEEP`. Word 0 is the
   initial stack pointer (`_estack`, top of SRAM), word 1 the reset handler.
   A bootloader locates an image's entry point by reading these two words
   from the slot base.
2. `.fw_info`: a small descriptor (magic `OTAL`, version string) at a fixed
   offset of 392 bytes (98 vectors x 4). The signed header of later phases
   will live here.
3. `.text`, `.rodata`, `.ARM.extab`, `.ARM.exidx`: code, constants, unwind
   tables. `_etext` marks the end of code.
4. `.data`: initialised variables. VMA in SRAM (`_sdata`..`_edata`), LMA in
   flash right after `.rodata` (`_sidata`). `Reset_Handler` copies it over.
   `_eimage` marks the end of the flash image, which is what gets hashed and
   signed later.

Sections that occupy RAM only (`NOLOAD`):

5. `.bss`: zero initialised variables, cleared by `Reset_Handler`.
6. `._user_heap_stack`: reserves 2 KiB so the link fails if static data
   grows into the stack area.
7. `.noinit` in CCM RAM: untouched by startup, intended for bootloader and
   application to exchange state across a reset (boot reason, requested
   slot, update result).

`Reset_Handler` also writes `SCB->VTOR` with the table's address. That is a
no-op at 0x08000000 but essential once the app runs from a slot.

### Measured sizes (phase 1, `-O2`, Arm GNU Toolchain 14.2)

| Section            | Address    | ota_app | can_gateway |
|--------------------|------------|--------:|------------:|
| `.isr_vector`      | 0x08000000 |   392 B |       392 B |
| `.fw_info`         | 0x08000188 |    32 B |        32 B |
| `.text`            | 0x080001A8 |  1680 B |      1796 B |
| `.rodata`          |            |   172 B |        84 B |
| `.data`            | 0x20000000 |     0 B |         0 B |
| `.bss`             | 0x20000000 |   292 B |       540 B |
| `._user_heap_stack`|            |  2052 B |      2052 B |
| flash image total  |            |  2276 B |      2304 B |

`_eimage` for the app is 0x080008E4. Either image fits in a single 16 KiB
sector many times over; the 128 KiB slots leave ample room for the crypto
code that phase 2 adds.

## Firmware behaviour (phase 1)

`firmware/app/main.c`:

* TIM2 update interrupt at 100 Hz. The ISR toggles PD12 every 250 ms
  (2 Hz blink) and raises a heartbeat flag every 1000 ms.
* USART2 at 115200 8N1 prints a banner at boot, then one line per second:
  `HB seq=<n> uptime_ms=<n*1000> can_rx=<count> can_tx_fail=<count>`.
* CAN1 RX0 interrupt echoes every received frame with the identifier
  incremented by one (masked to 11 or 29 bits).
* The main loop sleeps in WFI and only drains the heartbeat counter.

No HAL, no libc. `fmt.c` provides the two number formatters that are needed.

## Test topology in Renode

```
 pytest (host)
   |  TCP                      TCP  |
   v                                v
 dut_term  <-- usart2 --+   +-- usart2 --> gw_term
                        |   |
                 [dut machine]   [gateway machine]
                   can1 |           | can1
                        +-- canbus -+        (emulation CreateCANHub)
```

Renode's SocketCAN bridge is Linux only, so instead a second emulated STM32F4
runs `firmware/can_gateway`, an SLCAN-style bridge. The host sends
`t1234DEADBEEF\r` on the gateway UART socket; the gateway answers `OK`,
transmits on the hub, the DUT echoes with ID+1, and the gateway prints
`t1244DEADBEEF`. Everything the host touches is a plain TCP socket, which is
portable and needs no kernel modules. This is the CAN injection path used by
the test suite. Direct injection from the monitor (Python `OnFrameReceived`
on `sysbus.can1`) was used once during bring-up to validate the DUT on its
own and remains available for debugging.

`tests/renode_harness.py` starts `renode --disable-gui -P <port>` with a
generated script that sets the firmware paths and ports, connects to the
monitor and both UART sockets, and only then issues `start`, so the boot
banner is captured. Each test gets a fresh Renode process. While waiting for
UART output the harness polls the Renode process and raises with the tail of
its output if it has exited.

Tests:

| Test | Asserts |
|------|---------|
| `test_boot_banner` | banner, `can1: ready`, `boot: ok` on the DUT UART |
| `test_heartbeat_rate` | contiguous sequence numbers, uptime = seq x 1000 ms, heartbeat count equals elapsed whole seconds of Renode virtual time |
| `test_led_blinks` | `sysbus.gpioPortD.UserLED State` read through the monitor takes both values |
| `test_can_echo_*` | standard, extended, boundary and burst frames come back with ID+1; the DLC 0 case is skipped (Renode bug) |

## CI and reproducibility

`.github/workflows/ci.yml` installs `gcc-arm-none-eabi`, downloads the Renode
portable tarball (cached by version), builds with CMake and Ninja, runs pytest
and uploads `test-logs/` (Renode log plus UART transcripts per test). A second
job builds the `Dockerfile` and runs the suite inside the container.

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
checkout at a short path; deep directories hit the 260 character limit when
the harness creates per-test log directories.

Docker:

```bash
docker build -t ota-lab . && docker run --rm ota-lab
```

## Phase roadmap

| Phase | Adds |
|-------|------|
| 1 | this foundation |
| 2 | A/B bootloader, signed image header, rollback, boot state sector |
| 3 | chunked firmware transfer over CAN (ISO-TP style), flash driver |
| 4 | fault injection (power cut mid-write, corrupted chunks, bad signatures) |
| 5 | fleet dashboard over many Renode instances |

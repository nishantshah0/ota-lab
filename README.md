# OTA lab

Secure over-the-air firmware update system for STM32F4, developed and tested
entirely in [Renode](https://renode.io) with a pytest harness. No hardware
required.

Phase 1 (current) is the foundation: a bare-metal STM32F407 application with
its own startup code and linker script, a two-machine Renode setup with a
shared CAN bus, and a test suite that drives it over TCP sockets. Later phases
add an A/B bootloader with signed images and rollback, chunked transfer over
CAN, fault injection, and a fleet view.

* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): design, memory map, linker
  sections, Renode quirks, test topology.
* [docs/PHASE1_NOTES.md](docs/PHASE1_NOTES.md): what broke during bring-up
  and why.

## Prerequisites

* Arm GNU Toolchain (`arm-none-eabi-gcc`), 10 or newer
* CMake 3.18+ and Ninja
* Renode 1.16.x, either on `PATH` or pointed to by `RENODE=/path/to/renode`
* Python 3.9+ with `pip install -r tests/requirements.txt`

Or skip all of that and use Docker:

```bash
docker build -t ota-lab . && docker run --rm ota-lab
```

Windows users: the toolchain, CMake, Ninja and Python all install via
`winget`; unpack the Renode Windows portable zip and set `RENODE` to
`renode.exe`. Keep the clone at a short path.

## Build

```bash
cmake -S . -B build -G Ninja && cmake --build build
```

Produces `build/firmware/app/ota_app.elf` and
`build/firmware/can_gateway/can_gateway.elf` (plus `.bin` and `.map`), and
prints the section sizes.

## Test

```bash
pytest -v
```

The suite builds the firmware if the ELFs are missing, launches Renode
headless once per test, and leaves logs in `test-logs/<test name>/` (Renode
log, Renode stdout, and both UART transcripts).

## Run interactively

```bash
renode renode/ota_lab.resc
```

Then in the monitor type `start`, and connect to the UARTs with
`telnet localhost 3456` (device under test) and `telnet localhost 3457`
(CAN gateway). Send `t1232AABB` followed by Enter on the gateway to put a CAN
frame on the bus; the device echoes it back as `t1242AABB`.

## License

MIT, see [LICENSE](LICENSE).

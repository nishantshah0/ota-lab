# Phase 2 bring-up notes

What broke while building the A/B bootloader, and what the cause was.
Design lives in [ARCHITECTURE.md](ARCHITECTURE.md); phase 1 history in
[PHASE1_NOTES.md](PHASE1_NOTES.md).

## Things that worked first time

* Ed25519 verification with the vendored Monocypher: the first bootloader
  build validated a signed image, rejected a flipped signature bit, and
  jumped. Monocypher compiles clean under `-Wall -Wextra -Werror` and needs
  no libc.
* The journal: torn record detection, highest-seq selection across banks,
  and the bank switch at 1024 records all behaved as designed on the first
  run of their tests.
* The live power-cut test. A Renode watchpoint on the CRC word of the
  attempt-increment record (`sysbus AddWatchpointHook <addr> 4 Write
  "cpu.IsHalted = True"`) halts the core with three words of the record in
  flash and no CRC; the flash is dumped through the monitor, Renode is
  killed, a fresh Renode boots from the dump, and the bootloader ignores the
  torn record.
* Safe mode, including keeping the watchdog fed while idle.

## Design decisions made during implementation

### Signature covers header + SHA-512(body), not header + body

Monocypher 4.0.2 only offers a one-shot `crypto_ed25519_check(sig, pk, msg,
len)`. The header lives at the slot base and the body 512 bytes later, so a
signature over the raw concatenation would need a contiguous copy, and a
128 KiB image does not fit in 128 KiB of SRAM next to the stack. Instead the
signed message is the 32 signed header bytes followed by SHA-512 of the
body, computed with Monocypher's streaming SHA-512 straight out of flash.
This is hash-then-sign with a collision resistant hash, which is how most
signature schemes are used in practice anyway; the Python signer does the
same thing so images verify with any standard Ed25519 library.

### Journal in two 16 KiB sectors, not one 64 KiB sector

Phase 1 planned "boot state in sector 4". A single sector means the erase
that happens when it fills is a moment where the only copy of the current
state is gone. Two banks in sectors 2 and 3 fix that: the bank that is not
current is erased, and it only ever holds records older than the current
one. The bootloader shrank to sectors 0..1 (32 KiB, 15.1 KiB used) to make
room; the safe-mode image took sector 4.

## Bugs fixed

### 1. Watchdog reset halted the core at address 0

Symptom: the first rollback test saw one trial boot and then silence. The
Renode log said `Watchdog reset triggered!`, then `ReadDoubleWord from non
existing peripheral at 0x0` and `PC does not lay in memory ... CPU was
halted`.

Cause: on a machine reset Renode's Cortex-M model re-reads the vector table
from address 0. Real STM32F4 parts alias flash at 0 through the boot pins;
Renode's STM32F4 platform does not, and instead runs a `reset` macro if the
script defines one (`No action for reset - macro dut.reset is not
registered`). Fix: `renode/ota_lab.resc` defines `macro reset` as
`sysbus LoadELF $boot_elf`, which re-points the core at 0x08000000 and
rewrites bytes identical to what is already in flash. Journal, slots and
boot log are untouched, exactly like a hardware reset.

### 2. Flash controller warnings flooded the log

Every programmed word produced two Renode warnings: the driver clears the
error flags in FLASH_SR and sets PG and PSIZE in FLASH_CR the way the
reference manual requires, and the model implements neither. Harmless (the
model programs by direct memory write), but a 16 byte record produced eight
warning lines. Fix: `logLevel 3 sysbus.flash_controller` in the script. The
driver keeps the real-hardware sequence.

### 3. Boot log `journal_seq` expectation

The rollback test expected the log entries to carry journal seq 5, 6, 7, 8.
The bootloader writes the attempt record first and then logs, so the
entries carry 6, 7, 8 and the rollback record 9. That is the more useful
meaning (which journal record this boot produced), so the test was changed,
not the firmware.

### 4. Heartbeat test window after the bootloader

`test_heartbeat_rate` compared the heartbeat count with elapsed virtual
time assuming the app started at time zero. With the bootloader in front
(signature check, journal write, log write) the app starts a little later.
The window now allows up to one second of boot overhead; the uptime values
the firmware prints are still required to be exact multiples of 1000 ms.

## Renode observations worth keeping

* `machine.RequestReset()` from the IWDG model preserves all memories
  (`MappedMemory.Reset()` is a no-op), including CCM, so the boot marker
  scheme behaves like hardware.
* `sysbus AddWatchpointHook` works on memory-mapped flash; `cpu` is
  available inside the hook script and `cpu.IsHalted = True` stops the
  core without pausing the emulation, which leaves the monitor usable for
  the flash dump.
* `sysbus.ReadBytes(addr, n)` from the monitor's `python` command is the
  simplest way to snapshot flash to a host file.

## Open items

* The DLC 0 CAN frame crash in Renode 1.16.1 is still unfixed upstream
  (see phase 1 notes).
* The safe-mode image can only report state; the update receiver arrives
  with the CAN transport in phase 3.
* The boot log erases itself when its sector fills (4096 entries); a
  device that boots that often would lose old history. Acceptable for a
  diagnostic log, noted for the fleet phase.

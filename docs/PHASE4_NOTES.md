# Phase 4 bring-up notes

Fault injection against the update path. The fault model and the invariant
list are in [ARCHITECTURE.md](ARCHITECTURE.md#fault-model-and-fault-injection).

## Method

Every power-cut test follows the same shape: arm a Renode watchpoint on the
flash word the cut should land on (`RenodeLab.arm_flash_write_cut`), start
the emulation, run the transfer in a thread, poll `sysbus.cpu IsHalted`
from the main thread, cancel the sender, dump flash through the monitor,
kill Renode, check invariants on the dump with the host-side codecs
(`tools/bootjournal.py`), and boot a fresh Renode from the dump. The
watchpoint fires before the watched write, so the flash content is exactly
what a power cut at that instant would leave.

Bit rot is a `flip_bits()` call on a dumped image. Bus faults are knobs on
the host sender. Fuzzing is random bytes on the protocol identifiers.

## Things that worked first time

* All power-cut scenarios on chunk writes and progress records: the
  invariant "every byte a progress record claims is in flash" held on
  every dump, and every resume landed at the previous window boundary.
* The confirm-write cut: the attempt record survives, trial 2 confirms.
* Bit rot in the slot body, the signature and the newest journal record.
* The seeded random campaign, both seeds, with three cuts each.

## Design change

A progress record with `chunks == total` is now resumable. Before, a power
cut between validation and the journal write at FINISH would have forced a
full retransfer even though every byte was in flash and verified. Now the
next START resumes at the end, the host sends nothing, FINISH validates
again and writes the journal record. Tested by
`test_power_cut_during_finish_journal_write`.

## Bugs fixed

### 1. Device inactivity timeout fired under bus faults

Symptom: with duplicates, reordering and loss combined, the device answered
`NOT_STARTED` in the middle of a transfer about three seconds of wall time
after the last frame it received.

Cause: the update task dropped its RAM state after 10 s without a data
frame, measured in device time. Renode's virtual clock runs faster than
wall time while a machine is busy, and the gateway spins in its pacing
loop, so 10 virtual seconds passed inside a 3 s host retry timeout. The
timeout was tuned against a host timer it cannot see. Fix on both sides:
the device timeout is now five minutes (it only frees RAM state; a new
START works in any state), and the host sender re-STARTs and resumes when
it receives `NOT_STARTED`, which also covers a device reboot in the middle
of a transfer.

### 2. Test invariant ignored the short last chunk

The check compared `chunks * 6` with the image length; the last chunk is
shorter, so a record for the complete image failed the check. Test fix.

### 3. Harness boot log parser crashed on a TORN entry

`read_bootlog()` split every token on `=`; a torn entry prints `TORN`
without one. The parser now flags torn entries. Also: after a torn entry
the next entry's sequence restarts from the last readable one, which the
test now expects.

### 4. Fuzzer produced DLC 0 frames

Random frame lengths included zero, which reaches the Renode 1.16.1 hub
crash documented in phase 1 through the gateway's transmit path. The
fuzzer sends 1 to 8 bytes.

## Observations

* A cut inside a window is the common case and costs at most 31 chunks of
  retransmission, since the record is only written per window.
* The bootloader's behaviour under a corrupted pending image (drop the
  pending flag, boot the active slot) means bit rot in the new image is
  repaired by simply sending it again.
* Renode's `AddWatchpointHook` plus `cpu.IsHalted` is precise to the
  instruction and needs no firmware hooks, which keeps the firmware
  identical to what would ship.

## Open items

* The random campaign uses three cuts per seed to keep the suite under
  ten minutes; a longer soak with more seeds would be a nightly job.
* No fault is injected into the bootloader's own writes beyond the phase 2
  journal cut; the boot log write and the safe-mode path are untested
  under power loss.
* Bit rot in the bootloader or safe-mode image is outside the design's
  recovery scope and is not tested.

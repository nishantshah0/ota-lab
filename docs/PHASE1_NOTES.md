# Phase 1 bring-up notes

What broke while getting the phase 1 foundation to build, run and pass, and
what the cause turned out to be. Kept short on purpose; the design lives in
[ARCHITECTURE.md](ARCHITECTURE.md).

## Environment

Host for bring-up: Windows 11, Renode 1.16.1 (windows-portable-dotnet),
Arm GNU Toolchain 14.2.Rel1, CMake 4.4.3, Ninja 1.13.2, Python 3.12.10.
CI: Ubuntu 22.04, apt `gcc-arm-none-eabi` 10.3, Renode 1.16.1 linux-portable.

## Things that worked first time

* Both firmware targets compiled clean with `-Wall -Wextra -Werror`.
* Boot banner, 1 Hz heartbeat and 2 Hz LED came up on the first Renode run.
  The 10 MHz `TIMER_CLOCK_HZ` assumption for Renode's timer model was right:
  the model reports frequency 10 MHz, divider 100, limit 999, so one tick is
  exactly 10 ms of virtual time.
* bxCAN filter setup was accepted by Renode's STMCAN model; a frame injected
  through the monitor came back with ID + 1 without any changes to `can.c`.
* The CAN gateway path (`t1234DEADBEEF` in, `OK` and `t1244DEADBEEF` out)
  worked on the first attempt, so the monitor-injection fallback was not
  needed.

## Bugs fixed

### 1. Log directory paths exceeded the Windows path limit

Cause: the project was first created inside a deeply nested scratch
directory; `test-logs/<test name>/` on top of that went past 260 characters
and `mkdir` failed. Fix: keep the checkout at a short path. No code change
was needed once the repository lived at a normal location, but the harness
now also sanitises parametrised test ids before using them as directory
names.

### 2. Gateway printed CAN frames from interrupt context

Cause: the first draft of `firmware/can_gateway/main.c` wrote the received
frame to the UART inside `CAN1_RX0_IRQHandler`. The echo from the DUT arrives
while the main loop is still printing `OK`, so the two strings could
interleave on the wire. Fix: the ISR pushes frames into a small ring buffer
and the main loop drains it after finishing whatever line it was writing.
Found by reading the code before the first run; never observed on the wire.

### 3. Empty CAN frames crash Renode (emulator bug, worked around)

Symptom: sending a DLC 0 frame through the gateway killed the whole Renode
process; the test saw only a timeout.

Cause: Renode 1.16.1's STMCAN model hands the CAN hub a `CANMessageFrame`
whose `Data` is null when DLC is 0. `CANHub.Transmit` encodes every frame
for its traffic log via `CANMessageFrame.ToSocketCAN`, catches only
`RecoverableException`, and the resulting `NullReferenceException` is
unhandled. Verified in the monitor that a frame built with an empty byte
array encodes fine, so the null originates in STMCAN. The upstream master
sources still have the same catch clause.

Fix: none in firmware, since DLC 0 is legal on real hardware and the code
path is correct. The DLC 0 parameter case is skipped with the reason string
above, and ID 0 is still covered with a one-byte payload. The harness now
polls the Renode process while waiting for UART output and fails within half
a second with the tail of Renode's stderr when the emulator dies.

### 4. Heartbeat test measured wall clock

The first version asserted that heartbeats arrived roughly one second apart
in host time. That is true on a lightly loaded machine (measured 1.00 s) but
is not something the firmware controls. Replaced by pausing the emulation
and comparing the heartbeat count with `machine ElapsedVirtualTime`. While
doing this the reported domain time was seen to trail CPU-visible time by
about 2 ms (the fourth heartbeat, uptime 4000 ms, was printed with the domain
clock at 3.998 s), so the check allows 50 ms of slack at the whole-second
boundaries. The uptime values printed by the firmware are still required to
be exact multiples of 1000 ms.

## Open items

* Report the DLC 0 crash upstream to Renode with the stack trace from the
  test log. Until it is fixed, no firmware on this bus may transmit an empty
  frame, which matters for the transport protocol design in phase 3.
* Docker was not available on the bring-up host; the `Dockerfile` is
  exercised only by the CI `docker` job.

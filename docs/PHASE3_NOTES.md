# Phase 3 bring-up notes

Chunked firmware delivery over CAN into the inactive slot. Design in
[ARCHITECTURE.md](ARCHITECTURE.md); earlier phases in
[PHASE1_NOTES.md](PHASE1_NOTES.md) and [PHASE2_NOTES.md](PHASE2_NOTES.md).

## Things that worked first time

* The device side of the protocol: START handshake, windowed receive into
  RAM, flash programming per window, progress records, FINISH validation
  with the shared `image_validate()`, and the journal write that marks the
  slot pending. The first complete transfer landed in slot B, was accepted,
  and after `reboot` the bootloader ran the trial and the new image
  confirmed.
* Anti-rollback, including the case where START lies about the version:
  the signed header decides at FINISH.
* Resume after reset: a transfer stopped after 320 chunks, the device was
  rebooted, and the next START resumed at 320 with no re-sent data.

## Bugs fixed

### 1. The gateway lost frames in a burst

Symptom: the very first transfer stalled at chunk 4 with a gateway `ERR`.

Cause: the host writes a whole 32 frame window (704 bytes of SLCAN text)
into the socket at once, Renode's UART model delivers it to the gateway
without pacing, and the gateway's 256 byte RX ring overflowed. Fix: the
ring is 4 KiB in every image (RAM is plentiful), so a window plus the
retransmit traffic fits.

### 2. The DUT lost frames in a burst

Symptom: with the gateway fixed, the DUT accepted two chunks out of every
burst and NAKed the rest.

Cause: Renode's CAN hub has no bus timing. The gateway forwarded 32 frames
with zero virtual time between them; the STM32 bxCAN receive FIFO holds
three frames, and the receive interrupt could not drain it between two
deliveries that happened at the same instant. On real hardware the bus
itself spaces frames by about 250 microseconds at 500 kbit/s. Fix: the
gateway paces its transmissions to that bus time using a microsecond
timer (`timer_micros()`), which restores the physics the model lacks
without touching the DUT. This is the same reason a bus analyser never
sees two frames at once.

### 3. Host waited for an ACK the device never sends

Symptom: after a NAK rewind the host sent 32 chunks from the rewind point
and then timed out forever.

Cause: the device acknowledges at window boundaries (multiples of 32
chunks), the host expected an ACK for "rewind point plus 32". Fix: the
host always sends up to the next boundary.

### 4. Give-up counter counted rewinds

Symptom: with 5% simulated loss the sender raised "gave up after 21
retransmits" even though every rewind made progress.

Cause: the retry limit counted every NAK rewind as a failed retry; a 3500
chunk transfer at 5% loss has about 180 of them. Fix: the limit now counts
consecutive ACK timeouts with no progress and resets whenever the device
accepts anything new.

### 5. Monocypher's `memset` did not link into the images

Symptom: `undefined reference to memset` from `libmonocypher.a` when the
application first linked the shared `image_validate()`.

Cause: GCC turns a clearing loop in Monocypher into a `memset` call; the
mini libc providing it lived in `libcommon.a`, which the linker had
already scanned. Fix: the mini libc is its own archive linked last.

## Measurements

* Throughput with no loss: about 7.2 KB per virtual second for a 21 KB
  image (3500 chunks). The limit is the host to gateway UART path
  (22 characters of SLCAN text per 6 byte chunk at 115200 baud), not the
  CAN bus (250 microseconds per frame would allow 24 KB/s) and not the
  device (flash programming is per window).
* With 5% frame loss: 307 frames dropped out of 6485 sent, 162 NAKs, 176
  retransmits, accepted.
* Application image grew from 5.8 KB to 20.4 KB because it now links
  Monocypher for the FINISH validation. Bootloader unchanged at 15.1 KB.

## Open items

* Retransmission is go-back-N with one NAK per gap; a selective repeat
  would cut retransmits under heavy loss. Not needed at CAN error rates.
* The safe-mode image still cannot receive an update; the update task
  only runs in the application. Wiring it into safe mode is a small
  change now that the task is shared code.
* The host to gateway path could use a binary framing instead of SLCAN
  text to roughly double throughput.

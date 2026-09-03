#!/usr/bin/env python3
"""
Stream a signed image to the device over CAN through the gateway UART.

  python tools/ota_send.py --port 3457 --image build/firmware/app/app_good_B.signed.bin
      [--host 127.0.0.1] [--force] [--drop-rate 0.05 --seed 1] [--corrupt-chunk 37]
      [--stop-after 200] [--reboot-port 3456]

Protocol: see firmware/common/ota_proto.h. The sender keeps a window of 32
chunks in flight, waits for the device's ACK after every window, rewinds
to the sequence number named in a NAK, and retransmits the window on
timeout. Exit status 0 means the device accepted the image (verdict OK).

Test knobs:
  --drop-rate     probability of not transmitting a DATA frame (simulated loss)
  --corrupt-chunk flip one bit in the payload of that chunk
  --stop-after    send only that many chunks, then return (for resume tests)

The Sender class is importable; pytest drives it with a LineIO adapter over
the harness's existing gateway socket.
"""
from __future__ import annotations

import argparse
import random
import socket
import sys
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import otaimg  # noqa: E402

ID_CTRL, ID_DATA, ID_REPLY = 0x710, 0x711, 0x712
CHUNK, WINDOW = 6, 32
START_A, START_B, FINISH, ABORT, STATUS = 1, 2, 3, 4, 5
ACK, NAK, VERDICT, STATUS_REPLY = 0x20, 0x21, 0x23, 0x24
FLAG_FORCE = 0x01

CODES = {
    0: "OK", 1: "GAP", 2: "NOT_STARTED", 3: "BAD_SIZE", 4: "SLOT_BUSY", 5: "VERSION_LOW",
    6: "FLASH", 7: "INCOMPLETE", 8: "BAD_MAGIC", 9: "BAD_HEADER", 10: "WRONG_SLOT",
    11: "BAD_CRC", 12: "BAD_SIGNATURE", 13: "BAD_VECTORS", 14: "JOURNAL", 15: "TIMEOUT",
    16: "SEQUENCE",
}


class LineIO:
    """Minimal line transport: write bytes, read one text line with a timeout."""

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def readline(self, timeout: float) -> str | None:
        raise NotImplementedError


class SocketLineIO(LineIO):
    def __init__(self, host: str, port: int):
        self.sock = socket.create_connection((host, port), timeout=5.0)
        self.sock.settimeout(None)
        self.buf = b""

    def write(self, data: bytes) -> None:
        self.sock.sendall(data)

    def readline(self, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                return None
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line.decode("utf-8", "replace").rstrip("\r")

    def close(self) -> None:
        self.sock.close()


@dataclass
class Reply:
    type: int
    seq: int = 0
    code: int = 0
    detail: int = 0
    raw: bytes = b""


@dataclass
class Result:
    accepted: bool
    verdict: str
    resumed_from: int
    chunks_sent: int
    naks: int
    retransmits: int
    dropped: int
    elapsed_s: float
    log: list[str] = field(default_factory=list)


class TransferError(Exception):
    pass


class Sender:
    def __init__(self, io: LineIO, *, verbose: bool = True, ack_timeout: float = 5.0,
                 max_retries: int = 10, drop_rate: float = 0.0, seed: int = 0,
                 corrupt_chunk: int | None = None):
        self.io = io
        self.verbose = verbose
        self.ack_timeout = ack_timeout
        self.max_retries = max_retries
        self.rng = random.Random(seed)
        self.drop_rate = drop_rate
        self.corrupt_chunk = corrupt_chunk
        self.naks = 0
        self.retransmits = 0
        self.dropped = 0
        self.log: list[str] = []

    # --- wire helpers -------------------------------------------------

    def _say(self, msg: str) -> None:
        self.log.append(msg)
        if self.verbose:
            print(msg, flush=True)

    def send_frame(self, can_id: int, data: bytes) -> None:
        self.io.write(f"t{can_id:03X}{len(data)}{data.hex().upper()}\r".encode())

    def recv_reply(self, timeout: float) -> Reply | None:
        """Next device reply frame; gateway OK/ERR lines and other traffic are skipped."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            line = self.io.readline(remaining)
            if line is None:
                return None
            if line == "OK" or line == "":
                continue
            if line == "ERR":
                raise TransferError("gateway rejected a frame")
            if line.startswith("t") and len(line) >= 5 and int(line[1:4], 16) == ID_REPLY:
                data = bytes.fromhex(line[5:])
                if not data:
                    continue
                r = Reply(type=data[0], raw=data)
                if r.type == VERDICT:
                    r.code = data[1]
                    r.detail = int.from_bytes(data[2:6], "little")
                elif r.type in (ACK, NAK):
                    r.seq = int.from_bytes(data[1:3], "little")
                    r.code = data[3]
                elif r.type == STATUS_REPLY:
                    r.code = data[1]
                    r.seq = int.from_bytes(data[3:5], "little")
                    r.detail = int.from_bytes(data[5:7], "little")
                return r

    # --- protocol -----------------------------------------------------

    def start(self, image: bytes, slot: int, force: bool) -> Reply:
        h = otaimg.parse_header(image)
        header_crc = zlib.crc32(image[:otaimg.HEADER_SIZE]) & 0xFFFFFFFF
        size = len(image)
        a = bytes([START_A]) + size.to_bytes(4, "little") + bytes(h.version)
        b = bytes([START_B, FLAG_FORCE if force else 0, slot]) + header_crc.to_bytes(4, "little") + b"\xff"
        self.send_frame(ID_CTRL, a)
        self.send_frame(ID_CTRL, b)
        r = self.recv_reply(self.ack_timeout)
        if r is None:
            raise TransferError("no reply to START")
        return r

    def data_frame(self, image: bytes, seq: int) -> bytes:
        payload = image[seq * CHUNK:(seq + 1) * CHUNK]
        if self.corrupt_chunk is not None and seq == self.corrupt_chunk:
            payload = bytes([payload[0] ^ 0x01]) + payload[1:]
        return seq.to_bytes(2, "little") + payload

    def send_window(self, image: bytes, base: int, total: int) -> int:
        # The device acknowledges at window boundaries (multiples of WINDOW
        # chunks) and at the last chunk, so after a rewind the host must
        # send up to the next boundary, not a full window from the rewind
        # point, or it would wait for an ACK number the device never emits.
        end = min((base // WINDOW + 1) * WINDOW, total)
        dropped_here = []
        for seq in range(base, end):
            if self.drop_rate and self.rng.random() < self.drop_rate:
                self.dropped += 1
                dropped_here.append(seq)
                continue
            self.send_frame(ID_DATA, self.data_frame(image, seq))
        if self.verbose and (dropped_here or base % WINDOW):
            self._say(f"  sent {base}..{end - 1}, dropped {dropped_here}")
        return end

    def transfer(self, image: bytes, slot: int, *, force: bool = False,
                 stop_after: int | None = None) -> Result:
        t0 = time.monotonic()
        total = (len(image) + CHUNK - 1) // CHUNK
        r = self.start(image, slot, force)
        if r.type == NAK:
            verdict = CODES.get(r.code, str(r.code))
            self._say(f"START rejected: {verdict}")
            return Result(False, verdict, 0, 0, 0, 0, 0, time.monotonic() - t0, self.log)
        if r.type != ACK:
            raise TransferError(f"unexpected reply to START: {r}")
        base = r.seq
        resumed_from = base
        self._say(f"START ok: {'resume from' if r.code == 1 else 'fresh, next'} chunk {base} of {total}")

        sent = 0
        stalled = 0   # consecutive ACK timeouts with no progress
        while base < total:
            if stop_after is not None and sent >= stop_after:
                self._say(f"stopping after {sent} chunks as requested")
                return Result(False, "STOPPED", resumed_from, sent, self.naks, self.retransmits,
                              self.dropped, time.monotonic() - t0, self.log)
            end = self.send_window(image, base, total)
            sent += end - base
            # Wait for the ACK that covers this window; NAK means rewind.
            deadline = time.monotonic() + self.ack_timeout
            while True:
                r = self.recv_reply(max(0.0, deadline - time.monotonic()))
                if r is None:
                    self.retransmits += 1
                    stalled += 1
                    self._say(f"timeout waiting for ACK at {base}, resending window")
                    if stalled > self.max_retries:
                        raise TransferError(f"no progress after {stalled} timeouts at chunk {base}")
                    break
                if r.type == NAK:
                    self.naks += 1
                    if r.code == 1:
                        self._say(f"NAK: gap, rewinding to {r.seq}")
                        if r.seq > base:
                            stalled = 0      # the device accepted something new
                        base = r.seq
                        self.retransmits += 1
                        break
                    raise TransferError(f"device NAK during data: {CODES.get(r.code, r.code)} at {r.seq}")
                if r.type == ACK:
                    if r.seq >= end:
                        base = r.seq
                        stalled = 0
                        break
                    # ACK for an earlier window (late): keep waiting.
                    continue
            if self.verbose and base % (WINDOW * 8) == 0:
                self._say(f"  {base}/{total} chunks ({100 * base // total}%)")

        self.send_frame(ID_CTRL, bytes([FINISH]))
        r = self.recv_reply(max(self.ack_timeout, 10.0))
        if r is None or r.type != VERDICT:
            raise TransferError(f"no verdict after FINISH: {r}")
        verdict = CODES.get(r.code, str(r.code))
        elapsed = time.monotonic() - t0
        self._say(f"verdict: {verdict} (detail {r.detail}) after {elapsed:.1f}s, "
                  f"{sent} chunks sent, {self.naks} NAKs, {self.retransmits} retransmits, {self.dropped} dropped")
        return Result(r.code == 0, verdict, resumed_from, sent, self.naks, self.retransmits,
                      self.dropped, elapsed, self.log)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3457)
    ap.add_argument("--image", required=True)
    ap.add_argument("--slot", choices=["A", "B"], help="defaults to the slot in the image header")
    ap.add_argument("--force", action="store_true", help="allow a lower version than the running one")
    ap.add_argument("--drop-rate", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--corrupt-chunk", type=int)
    ap.add_argument("--stop-after", type=int)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    image = Path(args.image).read_bytes()
    slot = otaimg.slot_index(args.slot) if args.slot else otaimg.parse_header(image).target_slot
    io = SocketLineIO(args.host, args.port)
    sender = Sender(io, verbose=not args.quiet, drop_rate=args.drop_rate, seed=args.seed,
                    corrupt_chunk=args.corrupt_chunk)
    result = sender.transfer(image, slot, force=args.force, stop_after=args.stop_after)
    io.close()
    return 0 if result.accepted else 1


if __name__ == "__main__":
    sys.exit(main())

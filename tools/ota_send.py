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
  --dup-rate      probability of transmitting a DATA frame twice
  --reorder-rate  probability of swapping a DATA frame with the next one
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
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import otaimg  # noqa: E402

ID_CTRL, ID_DATA, ID_REPLY = 0x710, 0x711, 0x712   # node 0
NODE_STRIDE, MAX_NODE = 0x10, 14
CHUNK, WINDOW = 6, 32
START_A, START_B, FINISH, ABORT, STATUS, INFO, LOG_READ, REBOOT = 1, 2, 3, 4, 5, 6, 7, 8
ACK, NAK, VERDICT, STATUS_REPLY, INFO_REPLY, LOG_REPLY = 0x20, 0x21, 0x23, 0x24, 0x25, 0x26
REASONS = {0: "ACTIVE", 1: "PENDING_TRIAL", 2: "FALLBACK", 3: "ROLLBACK", 4: "SAFE_MODE", 0xFF: "none"}
CAUSES = {0: "POWER_ON", 1: "RESET_WHILE_RUNNING", 2: "APP_REQUEST"}
RESULTS = {0: "OK", 1: "BAD_MAGIC", 2: "BAD_HEADER", 3: "BAD_SIZE", 4: "WRONG_SLOT", 5: "BAD_CRC", 6: "BAD_SIGNATURE", 7: "BAD_VECTORS"}
SLOTS = {0: "A", 1: "B", 2: "SAFE", 0xFF: "none"}


def node_ids(node: int) -> tuple[int, int, int]:
    """(control, data, reply) identifiers of a node."""
    if not 0 <= node <= MAX_NODE:
        raise ValueError(f"node must be 0..{MAX_NODE}")
    ctrl = ID_CTRL + NODE_STRIDE * node
    return ctrl, ctrl + 1, ctrl + 2
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
                 corrupt_chunk: int | None = None, dup_rate: float = 0.0,
                 reorder_rate: float = 0.0, node: int = 0):
        self.io = io
        self.node = node
        self.id_ctrl, self.id_data, self.id_reply = node_ids(node)
        self.verbose = verbose
        self.ack_timeout = ack_timeout
        self.max_retries = max_retries
        self.rng = random.Random(seed)
        self.drop_rate = drop_rate
        self.dup_rate = dup_rate
        self.reorder_rate = reorder_rate
        self.corrupt_chunk = corrupt_chunk
        self.cancel = threading.Event()   # set from another thread to abort
        self.naks = 0
        self.retransmits = 0
        self.restarts = 0
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
            if self.cancel.is_set():
                raise TransferError("cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            line = self.io.readline(min(remaining, 0.5))
            if line is None:
                if time.monotonic() >= deadline:
                    return None
                continue
            if line == "OK" or line == "":
                continue
            if line == "ERR":
                raise TransferError("gateway rejected a frame")
            if line.startswith("t") and len(line) >= 5 and int(line[1:4], 16) == self.id_reply:
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

    # --- fleet queries ------------------------------------------------

    def info(self, timeout: float | None = None) -> dict | None:
        """INFO: running/active slot, version, boot count, last boot reason."""
        self.send_frame(self.id_ctrl, bytes([INFO]))
        deadline = time.monotonic() + (timeout or self.ack_timeout)
        while True:
            r = self.recv_reply(max(0.0, deadline - time.monotonic()))
            if r is None:
                return None
            if r.type != INFO_REPLY:
                continue
            d = r.raw
            return {
                "node": self.node,
                "running": SLOTS.get(d[1] & 0xF, str(d[1] & 0xF)),
                "active": SLOTS.get(d[1] >> 4, str(d[1] >> 4)),
                "version": f"{d[2]}.{d[3]}.{d[4]}",
                "version_tuple": (d[2], d[3], d[4]),
                "boot_count": int.from_bytes(d[5:7], "little"),
                "last_reason": REASONS.get(d[7], str(d[7])),
            }

    def read_log(self, timeout: float | None = None) -> list[dict]:
        """Every boot log entry, oldest first."""
        entries: list[dict] = []
        idx = 0
        while True:
            self.send_frame(self.id_ctrl, bytes([LOG_READ]) + idx.to_bytes(2, "little"))
            deadline = time.monotonic() + (timeout or self.ack_timeout)
            r = None
            while True:
                r = self.recv_reply(max(0.0, deadline - time.monotonic()))
                if r is None or (r.type == LOG_REPLY and int.from_bytes(r.raw[1:3], "little") == idx):
                    break
            if r is None:
                raise TransferError(f"no LOG reply for index {idx}")
            d = r.raw
            if d[3] == 0xFF:
                return entries
            if d[3] == 0xFE:
                entries.append({"index": idx, "torn": True})
            else:
                entries.append({
                    "index": idx, "torn": False,
                    "slot": SLOTS.get(d[3], str(d[3])), "reason": REASONS.get(d[4], str(d[4])),
                    "attempts": d[5], "cause": CAUSES.get(d[6], str(d[6])),
                    "result_a": RESULTS.get(d[7] >> 4, str(d[7] >> 4)),
                    "result_b": RESULTS.get(d[7] & 0xF, str(d[7] & 0xF)),
                })
            idx += 1

    def reboot(self, timeout: float | None = None) -> bool:
        """Ask the device to reset through the bootloader. True if it acknowledged."""
        self.send_frame(self.id_ctrl, bytes([REBOOT]))
        deadline = time.monotonic() + (timeout or self.ack_timeout)
        while True:
            r = self.recv_reply(max(0.0, deadline - time.monotonic()))
            if r is None:
                return False
            if r.type == ACK:
                return True

    # --- protocol -----------------------------------------------------

    def start(self, image: bytes, slot: int, force: bool) -> Reply:
        h = otaimg.parse_header(image)
        header_crc = zlib.crc32(image[:otaimg.HEADER_SIZE]) & 0xFFFFFFFF
        size = len(image)
        a = bytes([START_A]) + size.to_bytes(4, "little") + bytes(h.version)
        b = bytes([START_B, FLAG_FORCE if force else 0, slot]) + header_crc.to_bytes(4, "little") + b"\xff"
        self.send_frame(self.id_ctrl, a)
        self.send_frame(self.id_ctrl, b)
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
        pending_swap = None
        for seq in range(base, end):
            if self.cancel.is_set():
                raise TransferError("cancelled")
            if self.drop_rate and self.rng.random() < self.drop_rate:
                self.dropped += 1
                dropped_here.append(seq)
                continue
            frame = self.data_frame(image, seq)
            if pending_swap is not None:
                # Reordering: the previous frame goes out after this one.
                self.send_frame(self.id_data, frame)
                self.send_frame(self.id_data, pending_swap)
                pending_swap = None
                continue
            if self.reorder_rate and self.rng.random() < self.reorder_rate:
                pending_swap = frame
                continue
            self.send_frame(self.id_data, frame)
            if self.dup_rate and self.rng.random() < self.dup_rate:
                self.send_frame(self.id_data, frame)
        if pending_swap is not None:
            self.send_frame(self.id_data, pending_swap)
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
                    if r.code == 2:
                        # NOT_STARTED: the device lost its RAM state (timeout or
                        # reset). Its flash still knows the transfer: START again
                        # and continue from wherever it says.
                        self.restarts += 1
                        r2 = self.start(image, slot, force)
                        if r2.type != ACK:
                            raise TransferError(f"re-START after NOT_STARTED refused: {r2}")
                        self._say(f"device forgot the transfer, re-STARTed at chunk {r2.seq}")
                        base = r2.seq
                        stalled = 0
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

        self.send_frame(self.id_ctrl, bytes([FINISH]))
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
    ap.add_argument("--dup-rate", type=float, default=0.0, help="probability of sending a DATA frame twice")
    ap.add_argument("--reorder-rate", type=float, default=0.0, help="probability of swapping a DATA frame with the next")
    ap.add_argument("--stop-after", type=int)
    ap.add_argument("--node", type=int, default=0, help="target node id, 0..14")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    image = Path(args.image).read_bytes()
    slot = otaimg.slot_index(args.slot) if args.slot else otaimg.parse_header(image).target_slot
    io = SocketLineIO(args.host, args.port)
    sender = Sender(io, verbose=not args.quiet, drop_rate=args.drop_rate, seed=args.seed,
                    corrupt_chunk=args.corrupt_chunk, dup_rate=args.dup_rate,
                    reorder_rate=args.reorder_rate, node=args.node)
    result = sender.transfer(image, slot, force=args.force, stop_after=args.stop_after)
    io.close()
    return 0 if result.accepted else 1


if __name__ == "__main__":
    sys.exit(main())

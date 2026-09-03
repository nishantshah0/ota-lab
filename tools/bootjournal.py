"""
Host-side codec for the boot state journal and the boot event log.
Mirrors firmware/common/journal.c and bootlog.c.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

RECORD_SIZE = 16
LOG_ENTRY_SIZE = 32
SLOT_A, SLOT_B, SLOT_SAFE, SLOT_NONE = 0, 1, 2, 0xFF


@dataclass
class Record:
    seq: int
    active: int
    pending: int
    attempts: int
    confirmed: int


def pack_record(seq: int, active: int, pending: int, attempts: int, confirmed: int) -> bytes:
    body = struct.pack("<IBBBBI", seq, active, pending, attempts, confirmed, 0xFFFFFFFF)
    assert len(body) == 12
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def torn_record(seq: int, active: int, pending: int, attempts: int, confirmed: int, words_written: int) -> bytes:
    """A record whose write was cut after `words_written` 32-bit words."""
    full = pack_record(seq, active, pending, attempts, confirmed)
    return full[: 4 * words_written] + b"\xff" * (RECORD_SIZE - 4 * words_written)


def parse_bank(data: bytes) -> list[Record | None]:
    """Decode every non-erased record; None marks a torn or invalid one."""
    out: list[Record | None] = []
    for off in range(0, len(data), RECORD_SIZE):
        rec = data[off:off + RECORD_SIZE]
        if rec == b"\xff" * RECORD_SIZE:
            break
        if (zlib.crc32(rec[:12]) & 0xFFFFFFFF) != struct.unpack("<I", rec[12:])[0]:
            out.append(None)
            continue
        seq, active, pending, attempts, confirmed, _ = struct.unpack("<IBBBBI", rec[:12])
        out.append(Record(seq, active, pending, attempts, confirmed))
    return out


def current_state(bank0: bytes, bank1: bytes) -> Record | None:
    best = None
    for bank in (bank0, bank1):
        for rec in parse_bank(bank):
            if rec is not None and (best is None or rec.seq > best.seq):
                best = rec
    return best


@dataclass
class LogEntry:
    seq: int
    journal_seq: int
    slot: int
    reason: int
    attempts: int
    cause: int
    result_a: int
    result_b: int
    version: int


@dataclass
class Progress:
    seq: int
    header_crc: int
    image_size: int
    slot: int
    state: int      # 1 receiving, 2 done, 0xFE aborted
    chunks: int
    flags: int


PROGRESS_SIZE = 32


def parse_progress(data: bytes) -> list[Progress | None]:
    out: list[Progress | None] = []
    for off in range(0, len(data), PROGRESS_SIZE):
        r = data[off:off + PROGRESS_SIZE]
        if r == b"\xff" * PROGRESS_SIZE:
            break
        if (zlib.crc32(r[:28]) & 0xFFFFFFFF) != struct.unpack("<I", r[28:])[0]:
            out.append(None)
            continue
        seq, hcrc, size, slot, state, chunks, flags = struct.unpack("<IIIBBHB", r[:17])
        out.append(Progress(seq, hcrc, size, slot, state, chunks, flags))
    return out


def last_progress(data: bytes) -> Progress | None:
    for rec in reversed(parse_progress(data)):
        if rec is not None:
            return rec
    return None


def parse_log(data: bytes) -> list[LogEntry | None]:
    out: list[LogEntry | None] = []
    for off in range(0, len(data), LOG_ENTRY_SIZE):
        e = data[off:off + LOG_ENTRY_SIZE]
        if e == b"\xff" * LOG_ENTRY_SIZE:
            break
        if (zlib.crc32(e[:28]) & 0xFFFFFFFF) != struct.unpack("<I", e[28:])[0]:
            out.append(None)
            continue
        seq, jseq, slot, reason, attempts, cause, ra, rb = struct.unpack("<IIBBBBBB", e[:14])
        (version,) = struct.unpack("<I", e[16:20])
        out.append(LogEntry(seq, jseq, slot, reason, attempts, cause, ra, rb, version))
    return out

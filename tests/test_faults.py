"""
Phase 4: fault injection.

Power cuts are Renode watchpoints on a specific flash write: the core halts
before that write, the flash is dumped, Renode is killed, and a fresh
Renode boots from the dump. Bit rot is applied to the dumped image before
the restart. Bus faults come from the host sender's knobs. After every
restart the same invariants are checked from the flash dump and the UART:
the device boots the active slot (never safe mode), a slot is never pending
before FINISH was accepted, and every byte a progress record claims is in
flash.
"""
import logging
import random
import sys
import threading
import time
import zlib

import pytest

import bootjournal as bj
import flashimage
from renode_harness import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
import ota_send  # noqa: E402
import otaimg  # noqa: E402

from test_ota_transfer import HarnessLineIO, ready, transfer  # noqa: E402

log = logging.getLogger(__name__)

BOOT_TIMEOUT = 60.0
CHUNK = 6
WINDOW = 32
JOURNAL0 = flashimage.JOURNAL_BANK0
PROGRESS = flashimage.PROGRESS_ADDR
SLOT_B = flashimage.SLOT_B


# ------------------------------------------------------------------ helpers


def chunk_word_addr(seq: int) -> int:
    """Flash address of the word that holds the first byte of chunk seq."""
    return SLOT_B + (seq * CHUNK) & ~3


def progress_record_addr(index: int, word: int = 7) -> int:
    """Address of a word (0..7) of progress record index. Word 7 is the CRC."""
    return PROGRESS + index * 32 + word * 4


def journal_record_addr(index: int, word: int = 3) -> int:
    """Address of a word (0..3) of journal bank 0 record index. Word 3 is the CRC."""
    return JOURNAL0 + index * 16 + word * 4


def run_until_cut(lab, image, *, timeout=90.0, **sender_kw) -> tuple[bytes, ota_send.Sender]:
    """Run a transfer in a thread until the armed watchpoint halts the core,
    then dump flash and stop Renode. Returns the dump."""
    s = ota_send.Sender(HarnessLineIO(lab.gw_uart), verbose=False, ack_timeout=3.0, **sender_kw)
    outcome = {}

    def worker():
        try:
            outcome["result"] = s.transfer(image, otaimg.SLOT_B)
        except ota_send.TransferError as e:
            outcome["error"] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lab.is_halted():
            break
        if not t.is_alive():
            raise AssertionError(f"transfer finished before the cut fired: {outcome}")
        time.sleep(0.2)
    else:
        raise AssertionError("watchpoint never fired")
    s.cancel.set()
    t.join(10.0)
    lab.pause()
    dump = lab.dump_flash(lab.log_dir / "flash-after-cut.bin", size=flashimage.IMAGE_SIZE)
    lab.stop()
    return dump, s


def check_invariants(dump: bytes, image: bytes, *, allow_pending: bool = False) -> bj.Progress | None:
    """Invariants that must hold in flash after any cut."""
    state = bj.current_state(dump[0x8000:0xC000], dump[0xC000:0x10000])
    if not allow_pending:
        assert state is None or state.pending == bj.SLOT_NONE, f"slot pending before FINISH accepted: {state}"
    rec = bj.last_progress(dump[PROGRESS - flashimage.FLASH_BASE:PROGRESS - flashimage.FLASH_BASE + 0x20000])
    if rec is not None and rec.state == 1:
        claimed = min(rec.chunks * CHUNK, len(image))   # last chunk may be short
        in_flash = dump[SLOT_B - flashimage.FLASH_BASE:SLOT_B - flashimage.FLASH_BASE + claimed]
        assert in_flash == image[:claimed], "progress record claims bytes that are not in flash"
    return rec


def boot_active_a(lab):
    lab.dut_uart.expect(r"^decision: slot=A reason=ACTIVE$", BOOT_TIMEOUT)
    lab.dut_uart.expect(r"^boot: ok$", 10.0)
    lab.dut_uart.expect(r"^confirm: already active$", 15.0)
    lab.gw_uart.expect(r"^GW ready can1=ok$", BOOT_TIMEOUT)


def finish_and_boot_b(lab, image, expect_resume_from=None):
    r = transfer(lab, image)
    assert r.accepted and r.verdict == "OK", r.verdict
    if expect_resume_from is not None:
        assert r.resumed_from == expect_resume_from, f"resumed from {r.resumed_from}, expected {expect_resume_from}"
    lab.send_command("reboot")
    lab.dut_uart.expect(r"^decision: slot=B reason=PENDING_TRIAL attempt=1/3$", BOOT_TIMEOUT)
    lab.dut_uart.expect(r"^boot: ok$", 10.0)
    lab.dut_uart.expect(r"^confirm: written seq=\d+$", 15.0)
    assert lab.read_state()["active"] == "B"
    return r


# -------------------------------------------------------- power cut scenarios


@pytest.mark.parametrize("cut_chunk", [33, 1000, 3200])
def test_power_cut_during_chunk_write_resumes(flash, lab_factory, cut_chunk):
    """Cut while a window is being programmed: the partial window is not
    covered by any record, so the resume point is the previous window."""
    image = flash.image("B")
    total = (len(image) + CHUNK - 1) // CHUNK
    assert cut_chunk < total
    lab = lab_factory(flash.slot("A").build(), autostart=False)
    lab.arm_flash_write_cut(chunk_word_addr(cut_chunk))
    lab.monitor.command("start")
    ready(lab)
    dump, _ = run_until_cut(lab, image)

    rec = check_invariants(dump, image)
    assert rec is not None and rec.state == 1
    expected_resume = (cut_chunk // WINDOW) * WINDOW
    assert rec.chunks == expected_resume, f"record says {rec.chunks}, expected {expected_resume}"

    second = lab_factory(flash.from_dump(dump).build())
    boot_active_a(second)
    # Printed during init, before the banner boot_active_a consumed.
    assert any(l.text == f"update: resumable transfer on flash slot=B chunks={expected_resume}"
               for l in second.dut_uart.history), [l.text for l in second.dut_uart.history[-15:]]
    finish_and_boot_b(second, image, expect_resume_from=expected_resume)


def test_power_cut_during_progress_record_write(flash, lab_factory):
    """Cut on the CRC word of the sixth progress record: that record is
    torn, the fifth one stands, and the resume point is its chunk count."""
    image = flash.image("B")
    lab = lab_factory(flash.slot("A").build(), autostart=False)
    lab.arm_flash_write_cut(progress_record_addr(5, word=7))
    lab.monitor.command("start")
    ready(lab)
    dump, _ = run_until_cut(lab, image)

    records = bj.parse_progress(dump[PROGRESS - flashimage.FLASH_BASE:PROGRESS - flashimage.FLASH_BASE + 0x1000])
    assert len(records) == 6 and records[5] is None, f"expected a torn sixth record, got {records}"
    rec = check_invariants(dump, image)
    assert rec == records[4] and rec.chunks == 4 * WINDOW   # record 0 is START (0 chunks)

    second = lab_factory(flash.from_dump(dump).build())
    boot_active_a(second)
    finish_and_boot_b(second, image, expect_resume_from=4 * WINDOW)


def test_power_cut_during_finish_journal_write(flash, lab_factory):
    """Every chunk is in flash and validated; the cut lands on the CRC word
    of the journal record that would mark B pending. After the restart the
    device boots A with nothing pending, and a new START resumes at the end:
    zero chunks resent, FINISH accepted."""
    image = flash.image("B")
    total = (len(image) + CHUNK - 1) // CHUNK
    lab = lab_factory(flash.slot("A").build(), autostart=False)
    # Empty journal: the pending record will be bank 0 record 0.
    lab.arm_flash_write_cut(journal_record_addr(0, word=3))
    lab.monitor.command("start")
    ready(lab)
    dump, _ = run_until_cut(lab, image)

    rec = check_invariants(dump, image)
    assert rec is not None and rec.state == 1 and rec.chunks == total
    torn = bj.parse_bank(dump[0x8000:0x8000 + 64])
    assert torn == [None], f"expected exactly one torn journal record, got {torn}"

    second = lab_factory(flash.from_dump(dump).build())
    second.dut_uart.expect(r"^journal: seq=0 active=A pending=none attempts=0 confirmed=0$", BOOT_TIMEOUT)
    boot_active_a(second)
    r = finish_and_boot_b(second, image, expect_resume_from=total)
    assert r.chunks_sent == 0


def test_power_cut_during_confirm_write(flash, lab_factory):
    """B is on its first trial and cuts out while writing its confirm record.
    The attempt record survives, the torn confirm is ignored, and the next
    boot is trial 2 which confirms. No rollback, no brick."""
    image_b = flash.image("B")
    img = (flash.slot("A").slot("B")
                .state(active=bj.SLOT_A, pending=bj.SLOT_B, attempts=0, confirmed=1, seq=1)
                .build())
    lab = lab_factory(img, autostart=False)
    # Record 0 is the initial state, record 1 the bootloader's attempt
    # increment, record 2 will be the confirm: cut on its CRC word.
    lab.arm_flash_write_cut(journal_record_addr(2, word=3))
    lab.monitor.command("start")
    lab.dut_uart.expect(r"^decision: slot=B reason=PENDING_TRIAL attempt=1/3$", BOOT_TIMEOUT)
    lab.dut_uart.expect(r"^boot: ok$", 10.0)
    deadline = time.monotonic() + 30.0
    while not lab.is_halted():
        assert time.monotonic() < deadline, "confirm write never happened"
        time.sleep(0.2)
    lab.pause()
    dump = lab.dump_flash(lab.log_dir / "flash-after-cut.bin", size=flashimage.IMAGE_SIZE)
    lab.stop()

    bank = bj.parse_bank(dump[0x8000:0x8000 + 64])
    assert bank[1] == bj.Record(2, bj.SLOT_A, bj.SLOT_B, 1, 0) and bank[2] is None, bank
    check_invariants(dump, image_b, allow_pending=True)

    second = lab_factory(flash.from_dump(dump).build())
    second.dut_uart.expect(r"^journal: seq=2 active=A pending=B attempts=1 confirmed=0$", BOOT_TIMEOUT)
    second.dut_uart.expect(r"^decision: slot=B reason=PENDING_TRIAL attempt=2/3$", 10.0)
    second.dut_uart.expect(r"^confirm: written seq=4$", 30.0)
    state = second.read_state()
    assert state["active"] == "B" and state["pending"] == "none"


@pytest.mark.parametrize("seed", [7, 42])
def test_random_power_cut_campaign(flash, lab_factory, seed):
    """Three power cuts at random flash writes during one update, each
    followed by a cold restart and an invariant check, then the transfer
    completes and B boots."""
    rng = random.Random(seed)
    image = flash.image("B")
    total = (len(image) + CHUNK - 1) // CHUNK

    cuts = []
    for _ in range(3):
        if rng.random() < 0.7:
            cuts.append(("chunk", rng.randrange(WINDOW, total - 1)))
        else:
            cuts.append(("record", rng.randrange(1, total // WINDOW)))
    cuts.sort(key=lambda c: c[1] if c[0] == "chunk" else c[1] * WINDOW)
    log.info("seed %d cut plan: %s", seed, cuts)

    dump = None
    resume_at = 0
    for kind, n in cuts:
        addr = chunk_word_addr(n) if kind == "chunk" else progress_record_addr(n, word=rng.randrange(0, 8))
        builder = flash.from_dump(dump) if dump else flash.slot("A")
        lab = lab_factory(builder.build(), autostart=False)
        lab.arm_flash_write_cut(addr)
        lab.monitor.command("start")
        if dump:
            boot_active_a(lab)
        else:
            ready(lab)
        try:
            dump, _ = run_until_cut(lab, image, timeout=60.0)
        except AssertionError as e:
            if "finished before the cut" in str(e):
                # The cut address was already passed by an earlier run (records
                # are append-only, so record n may be behind us). Not a fault.
                log.info("cut %s %d not reached, transfer completed instead", kind, n)
                lab.send_command("reboot")
                lab.dut_uart.expect(r"^decision: slot=B reason=PENDING_TRIAL attempt=1/3$", BOOT_TIMEOUT)
                lab.dut_uart.expect(r"^confirm: written seq=\d+$", 30.0)
                return
            raise
        rec = check_invariants(dump, image)
        resume_at = rec.chunks if rec is not None and rec.state == 1 else 0
        log.info("after cut %s %d: progress record %s", kind, n, rec)

    final = lab_factory(flash.from_dump(dump).build())
    boot_active_a(final)
    finish_and_boot_b(final, image, expect_resume_from=resume_at)


# ------------------------------------------------------------------- bit rot


def _flip(rng, count, span_bits):
    return sorted(rng.sample(range(span_bits), count))


def test_bit_rot_in_slot_body_is_caught_and_repaired(flash, lab_factory):
    """Three random bits flip in B after a successful transfer. The
    bootloader rejects B (BAD_CRC), drops the pending flag and boots A;
    a new transfer replaces the image."""
    image = flash.image("B")
    lab = lab_factory(flash.slot("A").build())
    ready(lab)
    r = transfer(lab, image)
    assert r.accepted
    lab.pause()
    dump = lab.dump_flash(lab.log_dir / "flash-good.bin", size=flashimage.IMAGE_SIZE)
    lab.stop()

    rng = random.Random(99)
    bits = _flip(rng, 3, (len(image) - 512) * 8)
    second = lab_factory(flash.from_dump(dump).flip_bits(SLOT_B + 512, bits).build())
    second.dut_uart.expect(r"^slot B: BAD_CRC$", BOOT_TIMEOUT)
    second.dut_uart.expect(r"^pending slot invalid, dropping it$", 10.0)
    second.dut_uart.expect(r"^decision: slot=A reason=FALLBACK$", 10.0)
    second.dut_uart.expect(r"^boot: ok$", 10.0)
    second.dut_uart.expect(r"^confirm: (written seq=\d+|already active)$", 15.0)
    second.gw_uart.expect(r"^GW ready can1=ok$", BOOT_TIMEOUT)
    assert second.read_state()["pending"] == "none"

    # The DONE progress record does not match a RECEIVING one, so this is a
    # fresh transfer that erases the damaged slot first.
    r = transfer(second, image)
    assert r.accepted and r.resumed_from == 0
    second.send_command("reboot")
    second.dut_uart.expect(r"^decision: slot=B reason=PENDING_TRIAL attempt=1/3$", BOOT_TIMEOUT)
    second.dut_uart.expect(r"^confirm: written seq=\d+$", 30.0)


def test_bit_rot_in_signature_is_caught(flash, lab_factory):
    image = flash.image("B")
    img = (flash.slot("A").slot("B")
                .state(active=bj.SLOT_A, pending=bj.SLOT_B, attempts=0, confirmed=1, seq=1)
                .flip_bits(SLOT_B + 32, [5])          # one bit of the signature
                .build())
    lab = lab_factory(img)
    lab.dut_uart.expect(r"^slot B: BAD_SIGNATURE$", BOOT_TIMEOUT)
    lab.dut_uart.expect(r"^decision: slot=A reason=FALLBACK$", 10.0)
    del image


def test_bit_rot_in_journal_record_falls_back_to_previous(flash, lab_factory):
    """The newest journal record (pending B) is corrupted by one bit; its
    CRC fails, the previous record (A active, nothing pending) is used, and
    the bootloader boots A as ACTIVE rather than trialling B."""
    records = [
        bj.pack_record(1, bj.SLOT_A, bj.SLOT_NONE, 0, 1),
        bj.pack_record(2, bj.SLOT_A, bj.SLOT_B, 0, 0),
    ]
    img = (flash.slot("A").slot("B").journal(*records)
                .flip_bits(JOURNAL0 + 16, [6 * 8 + 1])   # attempts byte of record 2
                .build())
    lab = lab_factory(img)
    lab.dut_uart.expect(r"^journal: seq=1 active=A pending=none attempts=0 confirmed=1$", BOOT_TIMEOUT)
    lab.dut_uart.expect(r"^decision: slot=A reason=ACTIVE$", 10.0)


def test_bit_rot_in_boot_log_is_reported_not_fatal(flash, lab_factory):
    lab = lab_factory(flash.slot("A").build())
    ready(lab)
    lab.pause()
    dump = lab.dump_flash(lab.log_dir / "flash-good.bin", size=flashimage.IMAGE_SIZE)
    lab.stop()
    second = lab_factory(flash.from_dump(dump).flip_bits(flashimage.BOOTLOG_ADDR, [9 * 8]).build())  # reason byte of entry 0
    boot_active_a(second)
    entries = second.read_bootlog()
    assert [e["torn"] for e in entries] == [True, False]
    assert entries[1]["reason"] == "ACTIVE" and entries[1]["slot"] == "A"


# ------------------------------------------------------------------ bus faults


def test_bus_duplicates_reordering_and_loss(flash, lab_factory):
    lab = lab_factory(flash.slot("A").build())
    ready(lab)
    image = flash.image("B")
    s = ota_send.Sender(HarnessLineIO(lab.gw_uart), verbose=False, ack_timeout=3.0,
                        drop_rate=0.03, dup_rate=0.03, reorder_rate=0.02, seed=2024)
    r = s.transfer(image, otaimg.SLOT_B)
    (lab.log_dir / "transfer.txt").write_text("\n".join(s.log) + "\n")
    assert r.accepted and r.verdict == "OK"
    assert r.naks > 0
    assert lab.read_state()["pending"] == "B"


def test_garbage_and_malformed_frames_do_not_break_the_device(flash, lab_factory):
    """Random frames on the protocol identifiers while idle, then malformed
    control sequences, then a normal transfer must still succeed."""
    lab = lab_factory(flash.slot("A").build())
    ready(lab)
    image = flash.image("B")
    s = ota_send.Sender(HarnessLineIO(lab.gw_uart), verbose=False, ack_timeout=3.0)
    rng = random.Random(5)
    for _ in range(150):
        can_id = rng.choice([ota_send.ID_CTRL, ota_send.ID_DATA])
        # 1..8 bytes: a DLC 0 frame would hit the Renode 1.16.1 hub crash (phase 1 notes)
        s.send_frame(can_id, bytes(rng.randrange(256) for _ in range(rng.randrange(1, 9))))
    # Drain whatever the device answered (NAK NOT_STARTED and the like).
    while s.recv_reply(1.0) is not None:
        pass

    # START_B without START_A
    s.send_frame(ota_send.ID_CTRL, bytes([ota_send.START_B, 0, 1]) + b"\x00" * 5)
    r = s.recv_reply(3.0)
    assert r is not None and r.type == ota_send.NAK and ota_send.CODES[r.code] == "SEQUENCE"
    # oversize image
    s.send_frame(ota_send.ID_CTRL, bytes([ota_send.START_A]) + (0x30000).to_bytes(4, "little") + b"\x00\x03\x00")
    s.send_frame(ota_send.ID_CTRL, bytes([ota_send.START_B, 0, 1]) + b"\x00" * 5)
    r = s.recv_reply(3.0)
    assert r is not None and r.type == ota_send.NAK and ota_send.CODES[r.code] == "BAD_SIZE"
    # FINISH with nothing started
    s.send_frame(ota_send.ID_CTRL, bytes([ota_send.FINISH]))
    r = s.recv_reply(3.0)
    assert r is not None and r.type == ota_send.VERDICT and ota_send.CODES[r.code] == "NOT_STARTED"

    lab.dut_uart.expect(r"^HB seq=\d+", 5.0)   # still alive
    r = transfer(lab, image)
    assert r.accepted and r.verdict == "OK"
    assert lab.read_state()["pending"] == "B"

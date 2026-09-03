"""
Phase 3: chunked firmware delivery over CAN into the inactive slot.

The host side is tools/ota_send.py driving the gateway UART socket the
harness already holds. All timing is Renode virtual time; the host only
waits for frames and lines.
"""
import logging
import sys

import pytest

import bootjournal as bj
from renode_harness import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
import ota_send  # noqa: E402
import otaimg  # noqa: E402

log = logging.getLogger(__name__)

BOOT_TIMEOUT = 60.0
TRIAL_TIMEOUT = 40.0


class HarnessLineIO(ota_send.LineIO):
    """ota_send transport over the harness's gateway LineReader."""

    def __init__(self, reader):
        self.reader = reader

    def write(self, data: bytes) -> None:
        self.reader.write(data)

    def readline(self, timeout: float):
        try:
            return self.reader.readline(timeout).text
        except TimeoutError:
            return None


def ready(lab):
    lab.dut_uart.expect(r"^boot: ok$", BOOT_TIMEOUT)
    lab.dut_uart.expect(r"^confirm: (written seq=\d+|already active)$", 15.0)
    lab.gw_uart.expect(r"^GW ready can1=ok$", BOOT_TIMEOUT)


def sender(lab, **kw):
    return ota_send.Sender(HarnessLineIO(lab.gw_uart), verbose=False, ack_timeout=3.0, **kw)


def transfer(lab, image, slot=otaimg.SLOT_B, **kw):
    """Run a transfer and measure bytes per virtual second."""
    t0 = lab.virtual_time_s("dut")
    s = sender(lab, **{k: v for k, v in kw.items() if k in ("drop_rate", "seed", "corrupt_chunk")})
    try:
        result = s.transfer(image, slot, **{k: v for k, v in kw.items() if k in ("force", "stop_after")})
    finally:
        # Keep the sender's log even when the transfer raises.
        (lab.log_dir / "transfer.txt").write_text("\n".join(s.log) + "\n")
    t1 = lab.virtual_time_s("dut")
    result.virtual_s = t1 - t0
    result.bytes_per_virtual_s = len(image) / max(1e-6, result.virtual_s)
    log.info("transfer of %d bytes: %s in %.2f virtual s (%.0f B/s), %d chunks sent, %d NAKs, %d retransmits, %d dropped",
             len(image), result.verdict, result.virtual_s, result.bytes_per_virtual_s,
             result.chunks_sent, result.naks, result.retransmits, result.dropped)
    return result


# ----------------------------------------------------------- good path


def test_full_transfer_into_b_then_boots_b_and_confirms(flash, lab_factory, request):
    lab = lab_factory(flash.slot("A").build())
    ready(lab)
    image = flash.image("B")
    r = transfer(lab, image)
    assert r.accepted and r.verdict == "OK"
    assert r.resumed_from == 0 and r.naks == 0

    lab.dut_uart.expect(r"^update: START fresh n=\d+$", 5.0)
    lab.dut_uart.expect(r"^update: FINISH code=OK n=\d+$", 5.0)
    state = lab.read_state()
    assert state["pending"] == "B" and state["active"] == "A" and state["attempts"] == 0

    throughput = lab.log_dir / "throughput.txt"
    throughput.write_text(f"{len(image)} bytes in {r.virtual_s:.3f} virtual s = {r.bytes_per_virtual_s:.0f} B/s\n")
    request.node.user_properties.append(("bytes_per_virtual_s", round(r.bytes_per_virtual_s)))
    assert r.bytes_per_virtual_s > 200, "bus throughput collapsed"

    lab.send_command("reboot")
    lab.dut_uart.expect(r"^reboot: requested$", 5.0)
    lab.dut_uart.expect(r"^cause: (APP_REQUEST|RESET_WHILE_RUNNING) slot=A$", BOOT_TIMEOUT)
    lab.dut_uart.expect(r"^decision: slot=B reason=PENDING_TRIAL attempt=1/3$", 10.0)
    lab.dut_uart.expect(r"^slot: B \(pending, trial boot\)$", 10.0)
    lab.dut_uart.expect(r"^boot: ok$", 10.0)
    m, _ = lab.dut_uart.expect(r"^confirm: (written seq=\d+)$", 15.0)
    state = lab.read_state()
    assert state["active"] == "B" and state["pending"] == "none" and state["confirmed"] == 1


def test_transfer_survives_random_frame_loss(flash, lab_factory):
    lab = lab_factory(flash.slot("A").build())
    ready(lab)
    image = flash.image("B")
    r = transfer(lab, image, drop_rate=0.05, seed=1234)
    assert r.dropped > 0, "the loss simulation dropped nothing"
    assert r.naks > 0 and r.retransmits > 0
    assert r.accepted and r.verdict == "OK"
    assert lab.read_state()["pending"] == "B"


def test_reset_mid_transfer_resumes_from_last_good_chunk(flash, lab_factory):
    lab = lab_factory(flash.slot("A").build())
    ready(lab)
    image = flash.image("B")
    total = (len(image) + 5) // 6

    partial = transfer(lab, image, stop_after=320)   # ten windows
    assert partial.verdict == "STOPPED" and partial.chunks_sent == 320
    lab.dut_uart.expect(r"^update: START fresh n=\d+$", 5.0)
    lab.send_command("update")
    m, _ = lab.dut_uart.expect(r"^UPDATE state=RECEIVING slot=B next=(\d+) total=(\d+) .*progress_records=(\d+) last_record=RECEIVING/(\d+)$", 5.0)
    assert int(m.group(1)) == 320 and int(m.group(2)) == total
    assert int(m.group(4)) == 320, "progress record must match what is in flash"

    lab.send_command("reboot")
    lab.dut_uart.expect(r"^decision: slot=A reason=ACTIVE$", BOOT_TIMEOUT)
    lab.dut_uart.expect(r"^update: resumable transfer on flash slot=B chunks=320$", 10.0)
    lab.dut_uart.expect(r"^confirm: already active$", 15.0)   # the gateway did not reboot

    r = transfer(lab, image)
    assert r.resumed_from == 320, f"expected resume at chunk 320, got {r.resumed_from}"
    assert r.chunks_sent == total - 320
    assert r.accepted and r.verdict == "OK"
    lab.dut_uart.expect(r"^update: START resume n=320$", 5.0)
    assert lab.read_state()["pending"] == "B"


# ------------------------------------------------------------ rejections


def test_corrupted_chunk_is_caught_on_finish(flash, lab_factory):
    lab = lab_factory(flash.slot("A").build())
    ready(lab)
    image = flash.image("B")
    r = transfer(lab, image, corrupt_chunk=200)   # inside the body
    assert not r.accepted and r.verdict == "BAD_CRC"
    lab.dut_uart.expect(r"^update: FINISH code=BAD_CRC n=\d+$", 5.0)
    state = lab.read_state()
    assert state["pending"] == "none", "a corrupt image must never be marked pending"


def test_bad_signature_is_rejected_on_finish(flash, lab_factory):
    lab = lab_factory(flash.slot("A").build())
    ready(lab)
    r = transfer(lab, flash.image("B", kind="bad_signature"))
    assert not r.accepted and r.verdict == "BAD_SIGNATURE"
    assert lab.read_state()["pending"] == "none"


def test_lower_version_is_rejected_unless_forced(flash, lab_factory):
    lab = lab_factory(flash.slot("A").build())
    ready(lab)
    old = flash.image("B", version=(0, 1, 0))

    r = transfer(lab, old)
    assert not r.accepted and r.verdict == "VERSION_LOW"
    lab.dut_uart.expect(r"^update: START rejected code=VERSION_LOW n=\d+$", 5.0)
    assert lab.read_state()["pending"] == "none"

    r = transfer(lab, old, force=True)
    assert r.accepted and r.verdict == "OK"
    assert lab.read_state()["pending"] == "B"


def test_forged_version_in_start_does_not_bypass_anti_rollback(flash, lab_factory):
    """START claims a high version but the signed header says 0.1.0: the
    header is the authority, so FINISH rejects it."""
    lab = lab_factory(flash.slot("A").build())
    ready(lab)
    old = flash.image("B", version=(0, 1, 0))
    s = sender(lab)

    real_start = s.start

    def lying_start(image, slot, force):
        h = otaimg.parse_header(image)
        import zlib
        header_crc = zlib.crc32(image[:otaimg.HEADER_SIZE]) & 0xFFFFFFFF
        a = bytes([ota_send.START_A]) + len(image).to_bytes(4, "little") + bytes((9, 9, 9))
        b = bytes([ota_send.START_B, 0, slot]) + header_crc.to_bytes(4, "little") + b"\xff"
        s.send_frame(ota_send.ID_CTRL, a)
        s.send_frame(ota_send.ID_CTRL, b)
        return s.recv_reply(s.ack_timeout)

    s.start = lying_start
    r = s.transfer(old, otaimg.SLOT_B)
    assert not r.accepted and r.verdict == "VERSION_LOW"
    assert lab.read_state()["pending"] == "none"


def test_cannot_target_the_running_slot(flash, lab_factory):
    lab = lab_factory(flash.slot("A").build())
    ready(lab)
    r = transfer(lab, flash.image("A"), slot=otaimg.SLOT_A)
    assert not r.accepted and r.verdict == "SLOT_BUSY"


# ------------------------------------------------- end to end with phase 2


def test_never_confirming_update_rolls_back_to_a(flash, lab_factory):
    lab = lab_factory(flash.slot("A").build())
    ready(lab)
    r = transfer(lab, flash.image("B", variant="noconfirm"))
    assert r.accepted
    lab.send_command("reboot")

    for attempt in (1, 2, 3):
        lab.dut_uart.expect(rf"^decision: slot=B reason=PENDING_TRIAL attempt={attempt}/3$", TRIAL_TIMEOUT)
        lab.dut_uart.expect(r"^confirm: skipped \(noconfirm build\)$", 15.0)
    lab.dut_uart.expect(r"^decision: slot=A reason=ROLLBACK$", TRIAL_TIMEOUT)
    lab.dut_uart.expect(r"^boot: ok$", 10.0)
    lab.dut_uart.expect(r"^confirm: already active$", 15.0)

    state = lab.read_state()
    assert state["active"] == "A" and state["pending"] == "none"
    reasons = [e["reason"] for e in lab.read_bootlog()]
    assert reasons == ["ACTIVE", "PENDING_TRIAL", "PENDING_TRIAL", "PENDING_TRIAL", "ROLLBACK"]

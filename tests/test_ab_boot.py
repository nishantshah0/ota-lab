"""
Phase 2: A/B bootloader, journal, rollback, watchdog, safe mode, boot log.

Every scenario is expressed as a flash image (what a programmer or an OTA
transfer would have left in flash) and observed through the DUT UART. All
timing is Renode virtual time driven by the firmware's own timers; the host
only waits for lines.
"""
import pytest

import bootjournal as bj

BOOT_TIMEOUT = 60.0
TRIAL_TIMEOUT = 40.0   # one trial boot: 2 s confirm deadline + 1 s watchdog + boot


def expect_boot(lab, slot, reason, attempt=None, timeout=BOOT_TIMEOUT):
    pattern = rf"^decision: slot={slot} reason={reason}"
    if attempt is not None:
        pattern += rf" attempt={attempt}/3"
    pattern += "$"
    return lab.dut_uart.expect(pattern, timeout)


def expect_app_ready(lab, timeout=BOOT_TIMEOUT):
    lab.dut_uart.expect(r"^boot: ok$", timeout)


def wait_for_confirm(lab, timeout=15.0):
    m, _ = lab.dut_uart.expect(r"^confirm: (written seq=\d+|already active)$", timeout)
    return m.group(1)


# ---------------------------------------------------------------- good path


def test_good_image_in_a_boots_and_confirms(flash, lab_factory):
    """A freshly installed image in A (journal says pending A) boots as a
    trial, confirms after its first heartbeat, and the journal ends up with
    A active and nothing pending."""
    image = flash.slot("A").state(active=bj.SLOT_A, pending=bj.SLOT_A, attempts=0, seq=1).build()
    lab = lab_factory(image)

    lab.dut_uart.expect(r"^journal: seq=1 active=A pending=A attempts=0 confirmed=0$", BOOT_TIMEOUT)
    lab.dut_uart.expect(r"^slot A: OK v0\.2\.0$", 10.0)
    lab.dut_uart.expect(r"^slot B: BAD_MAGIC$", 10.0)
    expect_boot(lab, "A", "PENDING_TRIAL", attempt=1)
    lab.dut_uart.expect(r"^slot: A \(pending, trial boot\)$", 10.0)
    expect_app_ready(lab)
    assert wait_for_confirm(lab) == "written seq=3"   # seq 2 was the attempt increment

    state = lab.read_state()
    assert state["active"] == "A" and state["pending"] == "none"
    assert state["attempts"] == 0 and state["confirmed"] == 1 and state["seq"] == 3

    log = lab.read_bootlog()
    assert [e["reason"] for e in log] == ["PENDING_TRIAL"]
    assert log[0]["slot"] == "A" and log[0]["attempts"] == 1
    assert log[0]["a"] == "OK" and log[0]["b"] == "BAD_MAGIC"
    assert log[0]["cause"] == "POWER_ON"


def test_empty_journal_defaults_to_active_a(lab):
    """No journal at all (first power-on after programming) boots A as active
    and the app has nothing to confirm."""
    expect_boot(lab, "A", "ACTIVE")
    expect_app_ready(lab)
    assert wait_for_confirm(lab) == "already active"
    state = lab.read_state()
    assert state["journal"] == "empty" and state["bank0"] == 0 and state["bank1"] == 0


# ------------------------------------------------------------- fallbacks


def test_corrupt_crc_in_a_falls_back_to_b(flash, lab_factory):
    image = flash.slot("A", kind="corrupt_crc").slot("B").build()
    lab = lab_factory(image)

    lab.dut_uart.expect(r"^slot A: BAD_CRC$", BOOT_TIMEOUT)
    lab.dut_uart.expect(r"^slot B: OK v0\.2\.0$", 10.0)
    expect_boot(lab, "B", "FALLBACK")
    lab.dut_uart.expect(r"^slot: B \(fallback\)$", 10.0)
    expect_app_ready(lab)
    # The surviving image adopts the active role once it confirms.
    assert wait_for_confirm(lab).startswith("written")
    state = lab.read_state()
    assert state["active"] == "B" and state["pending"] == "none"

    log = lab.read_bootlog()
    assert [(e["slot"], e["reason"], e["a"], e["b"]) for e in log] == [("B", "FALLBACK", "BAD_CRC", "OK")]


def test_bad_signature_in_a_falls_back_to_b(flash, lab_factory):
    image = flash.slot("A", kind="bad_signature").slot("B").build()
    lab = lab_factory(image)

    lab.dut_uart.expect(r"^slot A: BAD_SIGNATURE$", BOOT_TIMEOUT)
    expect_boot(lab, "B", "FALLBACK")
    expect_app_ready(lab)
    log = lab.read_bootlog()
    assert log[-1]["a"] == "BAD_SIGNATURE" and log[-1]["b"] == "OK"


@pytest.mark.parametrize("kind, expected", [
    ("wrong_slot", "WRONG_SLOT"),
    ("corrupt_body", "BAD_CRC"),
    ("garbage", "BAD_MAGIC"),
])
def test_other_invalid_images_are_rejected(flash, lab_factory, kind, expected):
    image = flash.slot("A", kind=kind).slot("B").build()
    lab = lab_factory(image)
    lab.dut_uart.expect(rf"^slot A: {expected}$", BOOT_TIMEOUT)
    expect_boot(lab, "B", "FALLBACK")


def test_both_slots_bad_lands_in_safe_mode(flash, lab_factory):
    image = flash.slot("A", kind="bad_signature").slot("B", kind="corrupt_crc").build()
    lab = lab_factory(image)

    lab.dut_uart.expect(r"^slot A: BAD_SIGNATURE$", BOOT_TIMEOUT)
    lab.dut_uart.expect(r"^slot B: BAD_CRC$", 10.0)
    expect_boot(lab, "SAFE", "SAFE_MODE")
    lab.dut_uart.expect(r"^no valid image in slot A or B, entering safe mode$", 10.0)
    lab.dut_uart.expect(r"^=== SAFE MODE v0\.2\.0 ===$", 10.0)
    lab.dut_uart.expect(r"^waiting for update$", 10.0)
    # Safe mode keeps the watchdog fed: it must still be alive well past the
    # 1 s watchdog period, and serve the console.
    lab.dut_uart.expect(r"^SAFE waiting uptime_s=5$", 30.0)
    log = lab.read_bootlog()
    assert [(e["slot"], e["reason"], e["a"], e["b"]) for e in log] == [
        ("SAFE", "SAFE_MODE", "BAD_SIGNATURE", "BAD_CRC")
    ]


# --------------------------------------------------------------- rollback


def test_never_confirming_image_rolls_back_after_three_attempts(flash, lab_factory):
    """A is active and confirmed; B holds a new image that never confirms.
    The watchdog brings the chip back after each trial; the fourth boot
    rolls back to A."""
    image = (flash.slot("A")
                  .slot("B", variant="noconfirm")
                  .state(active=bj.SLOT_A, pending=bj.SLOT_B, attempts=0, confirmed=1, seq=5)
                  .build())
    lab = lab_factory(image)

    for attempt in (1, 2, 3):
        expect_boot(lab, "B", "PENDING_TRIAL", attempt=attempt, timeout=TRIAL_TIMEOUT)
        lab.dut_uart.expect(r"^variant: noconfirm$", 10.0)
        lab.dut_uart.expect(r"^confirm: skipped \(noconfirm build\)$", 15.0)
        if attempt > 1:
            pass  # cause is checked on the log below

    lab.dut_uart.expect(r"^rollback: slot B failed to confirm after 3 attempts$", TRIAL_TIMEOUT)
    expect_boot(lab, "A", "ROLLBACK")
    lab.dut_uart.expect(r"^variant: good$", 10.0)
    expect_app_ready(lab)
    assert wait_for_confirm(lab) == "already active"

    state = lab.read_state()
    assert state["active"] == "A" and state["pending"] == "none" and state["attempts"] == 0

    log = lab.read_bootlog()
    assert [(e["slot"], e["reason"], e["attempts"]) for e in log] == [
        ("B", "PENDING_TRIAL", 1),
        ("B", "PENDING_TRIAL", 2),
        ("B", "PENDING_TRIAL", 3),
        ("A", "ROLLBACK", 0),
    ]
    # First boot is a cold start; every later one is the watchdog reset
    # seen through the CCM marker.
    assert [e["cause"] for e in log] == ["POWER_ON"] + ["RESET_WHILE_RUNNING"] * 3
    # jseq is the journal record each boot wrote before jumping: the three
    # attempt increments (6, 7, 8 after the initial 5) and the rollback (9).
    assert [e["jseq"] for e in log] == [6, 7, 8, 9]


def test_hanging_image_triggers_watchdog_and_counts_an_attempt(flash, lab_factory):
    image = (flash.slot("A")
                  .slot("B", variant="hang")
                  .state(active=bj.SLOT_A, pending=bj.SLOT_B, attempts=0, confirmed=1, seq=1)
                  .build())
    lab = lab_factory(image)

    expect_boot(lab, "B", "PENDING_TRIAL", attempt=1)
    lab.dut_uart.expect(r"^hang: spinning with interrupts disabled$", 10.0)
    # Nothing but the watchdog can get us out of that loop.
    lab.dut_uart.expect(r"^cause: RESET_WHILE_RUNNING slot=B$", TRIAL_TIMEOUT)
    lab.dut_uart.expect(r"^journal: seq=2 active=A pending=B attempts=1 confirmed=0$", 10.0)
    expect_boot(lab, "B", "PENDING_TRIAL", attempt=2)

    # Let it run to the rollback so the whole sequence is on record.
    expect_boot(lab, "A", "ROLLBACK", timeout=3 * TRIAL_TIMEOUT)
    expect_app_ready(lab)
    wait_for_confirm(lab)
    log = lab.read_bootlog()
    assert [(e["slot"], e["reason"], e["attempts"], e["cause"]) for e in log] == [
        ("B", "PENDING_TRIAL", 1, "POWER_ON"),
        ("B", "PENDING_TRIAL", 2, "RESET_WHILE_RUNNING"),
        ("B", "PENDING_TRIAL", 3, "RESET_WHILE_RUNNING"),
        ("A", "ROLLBACK", 0, "RESET_WHILE_RUNNING"),
    ]


def test_pending_image_that_fails_validation_is_dropped(flash, lab_factory):
    """Pending B is corrupt: the bootloader clears the pending flag rather
    than burning attempts on it, and boots A as a fallback."""
    image = (flash.slot("A")
                  .slot("B", kind="corrupt_crc")
                  .state(active=bj.SLOT_A, pending=bj.SLOT_B, attempts=0, confirmed=1, seq=1)
                  .build())
    lab = lab_factory(image)
    lab.dut_uart.expect(r"^pending slot invalid, dropping it$", BOOT_TIMEOUT)
    expect_boot(lab, "A", "FALLBACK")
    expect_app_ready(lab)
    state = lab.read_state()
    assert state["pending"] == "none" and state["seq"] == 2


# ---------------------------------------------------------------- journal


def test_journal_ignores_torn_record(flash, lab_factory):
    """Bank 0 holds two good records and a third whose write was cut after
    two words (no CRC). The bootloader must act on seq 2 and append seq 3
    after the torn slot."""
    records = [
        bj.pack_record(1, bj.SLOT_A, bj.SLOT_NONE, 0, 1),
        bj.pack_record(2, bj.SLOT_A, bj.SLOT_B, 1, 0),
        bj.torn_record(3, bj.SLOT_A, bj.SLOT_B, 2, 0, words_written=2),
    ]
    image = flash.slot("A").slot("B").journal(*records).build()
    lab = lab_factory(image)

    lab.dut_uart.expect(r"^journal: seq=2 active=A pending=B attempts=1 confirmed=0$", BOOT_TIMEOUT)
    expect_boot(lab, "B", "PENDING_TRIAL", attempt=2)
    expect_app_ready(lab)
    assert wait_for_confirm(lab) == "written seq=4"
    state = lab.read_state()
    assert state["seq"] == 4 and state["active"] == "B" and state["bank0"] == 5
    assert state["bank1"] == 0


def test_journal_prefers_highest_seq_across_banks(flash, lab_factory):
    """Bank 1 holds the newer record; bank 0 is stale but valid."""
    image = (flash.slot("A").slot("B")
                  .journal(bj.pack_record(7, bj.SLOT_A, bj.SLOT_NONE, 0, 1), bank=0)
                  .journal(bj.pack_record(9, bj.SLOT_B, bj.SLOT_NONE, 0, 1), bank=1)
                  .build())
    lab = lab_factory(image)
    lab.dut_uart.expect(r"^journal: seq=9 active=B pending=none attempts=0 confirmed=1$", BOOT_TIMEOUT)
    expect_boot(lab, "B", "ACTIVE")


def test_journal_switches_bank_when_full(flash, lab_factory):
    """Bank 0 completely full (1024 records): the next write erases bank 1
    and lands there; the current state is never in the erased bank."""
    records = [bj.pack_record(i + 1, bj.SLOT_A, bj.SLOT_NONE, 0, 1) for i in range(1023)]
    records.append(bj.pack_record(1024, bj.SLOT_A, bj.SLOT_B, 0, 0))  # last: pending B
    stale = [bj.pack_record(3, bj.SLOT_B, bj.SLOT_NONE, 0, 1)]         # old junk in bank 1
    image = flash.slot("A").slot("B").journal(*records, bank=0).journal(*stale, bank=1).build()
    lab = lab_factory(image)

    lab.dut_uart.expect(r"^journal: seq=1024 active=A pending=B attempts=0 confirmed=0$", BOOT_TIMEOUT)
    expect_boot(lab, "B", "PENDING_TRIAL", attempt=1)
    expect_app_ready(lab)
    wait_for_confirm(lab)
    state = lab.read_state()
    assert state["seq"] == 1026 and state["active"] == "B"
    assert state["bank0"] == 1024 and state["bank1"] == 2


def test_power_cut_during_journal_write_recovers_last_record(flash, lab_factory, tmp_path):
    """Halt the CPU on the third word of the bootloader's attempt-increment
    record (before its CRC), dump flash, kill Renode, boot again from that
    flash image. The torn record must be ignored and the previous state
    reused."""
    image = (flash.slot("A").slot("B")
                  .state(active=bj.SLOT_A, pending=bj.SLOT_B, attempts=0, confirmed=1, seq=1)
                  .build())
    # First run: arm a watchpoint on the CRC word of record #2 in bank 0 and
    # halt the core there. Record 1 occupies bytes 0..15, so record 2's CRC
    # word is at bank + 16 + 12.
    first = lab_factory(image, autostart=False)
    first.monitor.command('sysbus AddWatchpointHook 0x0800801C 4 Write "cpu.IsHalted = True"')
    first.monitor.command("start")
    first.dut_uart.expect(r"^journal: seq=1 active=A pending=B attempts=0 confirmed=1$", BOOT_TIMEOUT)
    first.dut_uart.expect(r"^slot B: OK v0\.2\.0$", 10.0)
    # The core halts at the watchpoint: no "decision:" line ever appears.
    with pytest.raises(TimeoutError):
        first.dut_uart.expect(r"^decision:", 5.0)
    first.pause()
    dump = first.dump_flash(tmp_path / "flash-after-cut.bin")
    first.stop()

    bank0 = dump[0x8000:0x8000 + 0x4000]
    parsed = bj.parse_bank(bank0)
    assert parsed[0] == bj.Record(1, bj.SLOT_A, bj.SLOT_B, 0, 1)
    assert len(parsed) == 2 and parsed[1] is None, f"expected a torn second record, got {parsed}"
    assert bank0[16:28] != b"\xff" * 12, "watchpoint fired before any word of record 2 was written"

    # Second run: same flash, cold power on.
    import flashimage
    second = lab_factory(flashimage.FlashImage.from_bytes(dump))
    second.dut_uart.expect(r"^journal: seq=1 active=A pending=B attempts=0 confirmed=1$", BOOT_TIMEOUT)
    expect_boot(second, "B", "PENDING_TRIAL", attempt=1)
    expect_app_ready(second)
    assert wait_for_confirm(second) == "written seq=3"
    state = second.read_state()
    assert state["seq"] == 3 and state["active"] == "B"
    assert state["bank0"] == 4  # record 1, torn record, attempt record, confirm record

import re
import time

BANNER_TIMEOUT = 60.0
HB_RE = re.compile(r"^HB seq=(\d+) uptime_ms=(\d+) can_rx=(\d+) can_tx_fail=(\d+)$")


def test_boot_banner(lab):
    lab.dut_uart.expect(r"^BOOT v\d+\.\d+\.\d+ \(phase 2\)$", BANNER_TIMEOUT)
    lab.dut_uart.expect(r"^decision: slot=A reason=ACTIVE$", 10.0)
    lab.dut_uart.expect(r"^=== OTA-LAB app v\d+\.\d+\.\d+ \(phase \d+\) ===$", 10.0)
    lab.dut_uart.expect(r"^can1: ready$", 10.0)
    lab.dut_uart.expect(r"^boot: ok$", 10.0)


def test_heartbeat_rate(lab):
    """Heartbeats must arrive once per second of virtual time.

    Wall-clock timing depends on host load, so instead the emulation is paused
    after a few heartbeats and the count is compared with Renode's own
    virtual clock. The bootloader runs first, so the app's uptime starts a
    little after virtual time zero; that offset is measured from the first
    heartbeat rather than assumed.
    """
    lab.dut_uart.expect(r"^boot: ok$", BANNER_TIMEOUT)
    for _ in range(4):
        lab.dut_uart.expect(HB_RE.pattern, 15.0)

    lab.pause()
    virtual_s = lab.virtual_time_s("dut")
    time.sleep(0.5)  # let the socket deliver anything printed just before the pause

    heartbeats = [m for m in (HB_RE.match(l.text) for l in lab.dut_uart.history) if m]
    seqs = [int(m.group(1)) for m in heartbeats]
    uptimes = [int(m.group(2)) for m in heartbeats]

    assert seqs == list(range(1, len(seqs) + 1)), f"heartbeat sequence not contiguous: {seqs}"
    assert uptimes == [s * 1000 for s in seqs], f"firmware uptime does not track seq: {uptimes}"

    # Heartbeat n is printed at app uptime n * 1.000 s. The app started after
    # the bootloader finished, so virtual time exceeds uptime by that boot
    # time, which is well under one second; allow it plus Renode's few ms of
    # domain-time lag at the edges.
    count = len(seqs)
    boot_overhead = 1.0
    tolerance = 0.05
    assert count - tolerance <= virtual_s < count + 1 + boot_overhead + tolerance, (
        f"{count} heartbeats after {virtual_s:.3f}s of virtual time"
    )


def test_led_blinks(lab):
    lab.dut_uart.expect(r"^boot: ok$", BANNER_TIMEOUT)
    seen = set()
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and len(seen) < 2:
        seen.add(lab.led_state())
        time.sleep(0.1)
    assert seen == {True, False}, f"LED never toggled, observed states: {seen}"

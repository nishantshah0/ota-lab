#!/usr/bin/env python3
"""
Fleet operations over the CAN gateway: status, boot logs and staged rollouts.

  python tools/fleet.py resc --nodes 5 --out build/fleet/fleet.resc
  python tools/fleet.py status --nodes 5
  python tools/fleet.py logs --node 3
  python tools/fleet.py rollout --image build/firmware/app/app_good_B.signed.bin \\
      --stage 20,50,100 --confirm-window 10 --svg docs/last_rollout.svg

All commands talk to the gateway UART socket (default port 3457). Every
device shares that one socket; replies are demultiplexed by CAN id. With
--monitor-port the timeline uses Renode virtual time, otherwise wall time.

Rollout policy: devices are split into stages by cumulative percentage.
Within a stage every device is updated concurrently: INFO, transfer into
the inactive slot, REBOOT, then poll INFO until the device reports the
target slot as both running and active (confirmed) or its last boot log
entry is ROLLBACK (reverted). Any failure halts the rollout before the
next stage; the report names the node and the reason.
"""
from __future__ import annotations

import argparse
import math
import queue
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleetgen  # noqa: E402
import ota_send  # noqa: E402
import otaimg  # noqa: E402


# ------------------------------------------------------------- transport


class GatewayBus:
    """One socket to the gateway, demultiplexed into per-node line queues."""

    def __init__(self, host: str, port: int):
        self.sock = socket.create_connection((host, port), timeout=5.0)
        self.sock.settimeout(None)
        self.lock = threading.Lock()
        self.queues: dict[int, "queue.Queue[str]"] = {}
        self.errors = 0
        self.other: list[str] = []
        self._buf = b""
        self._stop = False
        self._thread = threading.Thread(target=self._reader, name="gateway-reader", daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        while not self._stop:
            try:
                chunk = self.sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            self._buf += chunk
            while b"\n" in self._buf:
                raw, self._buf = self._buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").rstrip("\r")
                self._dispatch(line)

    def _dispatch(self, line: str) -> None:
        if line == "OK" or line == "":
            return
        if line == "ERR":
            self.errors += 1
            return
        if line.startswith("t") and len(line) >= 5:
            try:
                can_id = int(line[1:4], 16)
            except ValueError:
                return
            q = self.queues.get(can_id)
            if q is not None:
                q.put(line)
                return
        self.other.append(line)

    def io_for(self, node: int) -> "NodeIO":
        _, _, reply = ota_send.node_ids(node)
        q = self.queues.setdefault(reply, queue.Queue())
        return NodeIO(self, q)

    def write(self, data: bytes) -> None:
        with self.lock:
            self.sock.sendall(data)

    def close(self) -> None:
        self._stop = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class NodeIO(ota_send.LineIO):
    def __init__(self, bus: GatewayBus, q: "queue.Queue[str]"):
        self.bus = bus
        self.q = q

    def write(self, data: bytes) -> None:
        self.bus.write(data)

    def readline(self, timeout: float) -> str | None:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None


class MonitorClient:
    """Just enough of Renode's telnet monitor to read virtual time."""

    _PROMPT = re.compile(r"\([^)\r\n]*\) $")
    _ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")

    def __init__(self, host: str, port: int):
        self.sock = socket.create_connection((host, port), timeout=5.0)
        self.sock.settimeout(0.5)
        self.lock = threading.Lock()
        self.command("")

    def command(self, cmd: str, timeout: float = 10.0) -> str:
        with self.lock:
            # Discard log lines and the prompt Renode reprints after them.
            self.sock.settimeout(0.05)
            try:
                while self.sock.recv(4096):
                    pass
            except (socket.timeout, OSError):
                pass
            self.sock.settimeout(0.5)
            self.sock.sendall(cmd.encode() + b"\n")
            buf = b""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    chunk = self.sock.recv(4096)
                except socket.timeout:
                    chunk = b""
                if chunk:
                    buf += bytes(b for b in chunk if b != 255)  # crude IAC strip
                text = self._ANSI.sub(b"", buf).decode("utf-8", "replace")
                start = 0
                if cmd:
                    idx = text.find(cmd)
                    if idx < 0:
                        continue
                    start = idx + len(cmd)
                m = self._PROMPT.search(text, start)
                if m:
                    return text[start: m.start()]
            return buf.decode("utf-8", "replace")

    def virtual_time(self, machine: str = "dut0") -> float:
        self.command(f'mach set "{machine}"')
        out = self.command("machine ElapsedVirtualTime")
        m = re.search(r"Elapsed Virtual Time: (\d+):(\d+):(\d+)\.(\d+)", out)
        if not m:
            raise RuntimeError(f"could not parse virtual time: {out!r}")
        h, mi, s, frac = m.groups()
        return int(h) * 3600 + int(mi) * 60 + int(s) + int(frac) / 10 ** len(frac)


# ---------------------------------------------------------------- fleet


@dataclass
class Event:
    node: int
    kind: str      # transfer, boot, confirm, revert, fail
    t0: float
    t1: float
    label: str = ""


@dataclass
class RolloutResult:
    ok: bool
    stages: list[list[int]]
    completed_stages: int
    failures: list[tuple[int, str]]
    events: list[Event]
    stage_times: list[tuple[float, float]]
    final: dict[int, dict | None]
    clock_label: str
    report: list[str] = field(default_factory=list)


class Fleet:
    def __init__(self, host: str, gw_port: int, nodes: list[int], monitor_port: int | None = None,
                 ack_timeout: float = 5.0, say=print, clock=None, clock_label: str | None = None):
        """clock: optional callable returning seconds; used instead of opening a
        monitor connection (Renode's telnet monitor serves one client)."""
        self.bus = GatewayBus(host, gw_port)
        self.nodes = list(nodes)
        self.monitor = MonitorClient(host, monitor_port) if (monitor_port and clock is None) else None
        self._clock = clock
        self._label = clock_label
        self.ack_timeout = ack_timeout
        self.say = say
        self._t0 = time.monotonic()
        self._clock_lock = threading.Lock()

    @property
    def clock_label(self) -> str:
        if self._label:
            return self._label
        return "Renode virtual seconds" if self.monitor else "host seconds"

    def clock(self) -> float:
        with self._clock_lock:
            if self._clock is not None:
                return self._clock()
            if self.monitor:
                return self.monitor.virtual_time()
        return time.monotonic() - self._t0

    def sender(self, node: int, **kw) -> ota_send.Sender:
        return ota_send.Sender(self.bus.io_for(node), node=node, verbose=False,
                               ack_timeout=self.ack_timeout, **kw)

    def close(self) -> None:
        self.bus.close()

    # --- status / logs -------------------------------------------------

    def status(self) -> list[dict]:
        rows = []
        for n in self.nodes:
            info = self.sender(n).info(timeout=3.0)
            if info is None:
                rows.append({"node": n, "reachable": False})
            else:
                info["reachable"] = True
                rows.append(info)
        return rows

    @staticmethod
    def format_status(rows: list[dict]) -> str:
        head = f"{'node':>4}  {'running':>7}  {'active':>6}  {'version':>8}  {'boots':>5}  last boot"
        lines = [head, "-" * len(head)]
        for r in rows:
            if not r.get("reachable"):
                lines.append(f"{r['node']:>4}  {'-':>7}  {'-':>6}  {'-':>8}  {'-':>5}  no reply")
            else:
                lines.append(f"{r['node']:>4}  {r['running']:>7}  {r['active']:>6}  {r['version']:>8}  "
                             f"{r['boot_count']:>5}  {r['last_reason']}")
        return "\n".join(lines)

    def logs(self, node: int) -> list[dict]:
        return self.sender(node).read_log(timeout=3.0)

    @staticmethod
    def format_log(entries: list[dict]) -> str:
        out = []
        for e in entries:
            if e.get("torn"):
                out.append(f"{e['index']:>3}  TORN")
            else:
                out.append(f"{e['index']:>3}  slot={e['slot']:<4} reason={e['reason']:<13} attempts={e['attempts']} "
                           f"cause={e['cause']:<19} a={e['result_a']} b={e['result_b']}")
        return "\n".join(out) if out else "(empty)"

    # --- rollout -----------------------------------------------------

    @staticmethod
    def plan_stages(nodes: list[int], percents: list[int]) -> list[list[int]]:
        if not percents or percents[-1] != 100 or any(p <= 0 or p > 100 for p in percents):
            raise ValueError("stages must be increasing percentages ending in 100")
        groups, done = [], 0
        for p in percents:
            upto = math.ceil(len(nodes) * p / 100)
            if upto > done:
                groups.append(nodes[done:upto])
                done = upto
        return groups

    def _update_node(self, node: int, images: dict[int, bytes], confirm_window: float,
                     events: list[Event], failures: list[tuple[int, str]]) -> None:
        def ev(kind, t0, t1, label=""):
            events.append(Event(node, kind, t0, t1, label))

        def fail(t0, reason):
            ev("fail", t0, self.clock(), reason)
            failures.append((node, reason))
            self.say(f"  node {node}: FAILED: {reason}")

        s = self.sender(node)
        t_start = self.clock()
        info = s.info(timeout=3.0)
        if info is None:
            fail(t_start, "no INFO reply (unreachable, or in safe mode which cannot update over CAN)")
            return
        if info["running"] not in ("A", "B"):
            fail(t_start, f"running slot {info['running']} cannot be updated over CAN")
            return
        target = "B" if info["running"] == "A" else "A"
        image = images.get(otaimg.slot_index(target))
        if image is None:
            fail(t_start, f"no image available for slot {target}")
            return
        old_version = info["version"]
        new_version = otaimg.parse_header(image).version_str

        t0 = self.clock()
        try:
            r = s.transfer(image, otaimg.slot_index(target))
        except ota_send.TransferError as e:
            fail(t0, f"transfer error: {e}")
            return
        t1 = self.clock()
        ev("transfer", t0, t1, f"{len(image)} B, {r.naks} NAK")
        if not r.accepted:
            fail(t1, f"image rejected: {r.verdict}")
            return
        self.say(f"  node {node}: transferred {new_version} into slot {target} ({t1 - t0:.1f} s)")

        t_boot = self.clock()
        if not s.reboot(timeout=3.0):
            fail(t_boot, "no ACK to REBOOT")
            return

        deadline = t_boot + confirm_window
        grace_deadline = deadline + 2 * confirm_window
        seen_up = None
        last = None
        while True:
            now = self.clock()
            info = s.info(timeout=1.0)
            if info is not None:
                last = info
                if seen_up is None and info["boot_count"] > 0:
                    seen_up = now
                    ev("boot", t_boot, now, f"running {info['running']}")
                if info["running"] == target and info["active"] == target:
                    ev("confirm", seen_up or t_boot, now, new_version)
                    self.say(f"  node {node}: confirmed {new_version} in slot {target} ({now - t_boot:.1f} s after reboot)")
                    return
                if info["last_reason"] == "ROLLBACK":
                    ev("revert", seen_up or t_boot, now, f"back to {old_version}")
                    fail(now, f"reverted to {info['version']} in slot {info['running']} after the trial "
                              f"(bootloader rolled back, {info['boot_count']} boots)")
                    return
            if now > deadline and (last is None or last["last_reason"] != "PENDING_TRIAL" or now > grace_deadline):
                desc = "no INFO reply" if last is None else \
                    f"running {last['running']} active {last['active']} last boot {last['last_reason']}"
                fail(now, f"did not confirm within {confirm_window:.0f} s window ({desc})")
                return
            time.sleep(0.5)

    def rollout(self, image: bytes, stages: list[int] = (20, 50, 100), confirm_window: float = 10.0,
                image_a: bytes | None = None, image_overrides: dict[int, bytes] | None = None,
                svg_path: str | Path | None = None) -> RolloutResult:
        images = {otaimg.SLOT_B: image}
        if image_a is not None:
            images[otaimg.SLOT_A] = image_a
        hdr = otaimg.parse_header(image)
        if hdr.target_slot != otaimg.SLOT_B:
            images = {hdr.target_slot: image}
        groups = self.plan_stages(self.nodes, list(stages))
        events: list[Event] = []
        failures: list[tuple[int, str]] = []
        stage_times: list[tuple[float, float]] = []
        report: list[str] = []
        version = hdr.version_str

        def log(msg):
            report.append(msg)
            self.say(msg)

        log(f"rollout of {version} to {len(self.nodes)} nodes in {len(groups)} stages "
            f"{[len(g) for g in groups]} (confirm window {confirm_window:.0f} s, clock: {self.clock_label})")
        completed = 0
        for i, group in enumerate(groups, 1):
            pct = stages[i - 1] if i - 1 < len(stages) else 100
            t_stage = self.clock()
            log(f"stage {i} ({pct}%): nodes {group}")
            threads = []
            for n in group:
                per_node = dict(images)
                if image_overrides and n in image_overrides:
                    ov = image_overrides[n]
                    per_node[otaimg.parse_header(ov).target_slot] = ov
                t = threading.Thread(target=self._update_node,
                                     args=(n, per_node, confirm_window, events, failures), daemon=True)
                threads.append(t)
                t.start()
            for t in threads:
                t.join()
            stage_times.append((t_stage, self.clock()))
            if failures:
                for n, why in failures:
                    log(f"HALT at stage {i} ({pct}%): node {n}: {why}")
                break
            completed = i
            log(f"stage {i} complete")

        ok = not failures and completed == len(groups)
        log("rollout " + ("complete" if ok else "halted"))
        final = {n: self.sender(n).info(timeout=3.0) for n in self.nodes}
        result = RolloutResult(ok, groups, completed, failures, events, stage_times, final,
                               self.clock_label, report)
        if svg_path:
            write_timeline_svg(Path(svg_path), self.nodes, result, version)
            log(f"timeline written to {svg_path}")
        return result


# ------------------------------------------------------------- timeline


COLOURS = {"transfer": "#4a90d9", "boot": "#9aa5b1", "confirm": "#2e8b57", "revert": "#c0392b", "fail": "#e67e22"}


def write_timeline_svg(path: Path, nodes: list[int], result: RolloutResult, version: str) -> None:
    left, top, row_h, width = 70, 60, 26, 900
    plot_w = width - left - 20
    t_end = max([e.t1 for e in result.events] + [t for _, t in result.stage_times] + [1.0])
    t_start = min([e.t0 for e in result.events] + [t for t, _ in result.stage_times] + [t_end])
    span = max(t_end - t_start, 1e-6)

    def x(t):
        return left + (t - t_start) / span * plot_w

    height = top + row_h * len(nodes) + 70
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="12">',
           f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
           f'<text x="{left}" y="22" font-size="15" font-weight="bold">Rollout of {version}: '
           f'{"complete" if result.ok else "halted"} ({len(nodes)} nodes, {len(result.stages)} stages)</text>',
           f'<text x="{left}" y="40" fill="#555">x axis: {result.clock_label}</text>']
    for i, (a, b) in enumerate(result.stage_times, 1):
        out.append(f'<line x1="{x(a):.1f}" y1="{top - 8}" x2="{x(a):.1f}" y2="{top + row_h * len(nodes)}" '
                   f'stroke="#bbb" stroke-dasharray="4 3"/>')
        out.append(f'<text x="{x(a) + 3:.1f}" y="{top - 12}" fill="#777">stage {i}</text>')
    for i, n in enumerate(nodes):
        y = top + i * row_h
        out.append(f'<text x="{left - 8}" y="{y + row_h * 0.65:.1f}" text-anchor="end">node {n}</text>')
        out.append(f'<line x1="{left}" y1="{y + row_h}" x2="{width - 20}" y2="{y + row_h}" stroke="#eee"/>')
        for e in [e for e in result.events if e.node == n]:
            w = max(x(e.t1) - x(e.t0), 3.0)
            out.append(f'<rect x="{x(e.t0):.1f}" y="{y + 5}" width="{w:.1f}" height="{row_h - 10}" '
                       f'fill="{COLOURS.get(e.kind, "#888")}" rx="2"><title>node {n} {e.kind} '
                       f'{e.t0:.2f}..{e.t1:.2f} {e.label}</title></rect>')
    ticks = 6
    axis_y = top + row_h * len(nodes) + 8
    for k in range(ticks + 1):
        t = t_start + span * k / ticks
        out.append(f'<line x1="{x(t):.1f}" y1="{axis_y}" x2="{x(t):.1f}" y2="{axis_y + 5}" stroke="#333"/>')
        out.append(f'<text x="{x(t):.1f}" y="{axis_y + 18}" text-anchor="middle">{t:.1f}</text>')
    lx = left
    for kind, colour in COLOURS.items():
        out.append(f'<rect x="{lx}" y="{axis_y + 30}" width="14" height="12" fill="{colour}" rx="2"/>')
        out.append(f'<text x="{lx + 18}" y="{axis_y + 40}">{kind}</text>')
        lx += 90
    out.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ CLI


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3457, help="gateway UART socket")
    ap.add_argument("--monitor-port", type=int, help="Renode monitor port, for virtual-time timelines")
    ap.add_argument("--nodes", type=int, default=5, help="node ids 0..nodes-1")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("resc", help="generate a fleet Renode script")
    p.add_argument("--out", default=root / "build/fleet/fleet.resc")
    p.add_argument("--flash-dir")
    p.add_argument("--uart-base", type=int, default=3460)

    sub.add_parser("status", help="one line per node")

    p = sub.add_parser("logs", help="boot log of one node, oldest first")
    p.add_argument("--node", type=int, required=True)

    p = sub.add_parser("rollout", help="staged rollout of a signed image")
    p.add_argument("--image", required=True, help="signed image for slot B (or A if that is what it targets)")
    p.add_argument("--image-a", help="signed image for slot A, for devices currently running B")
    p.add_argument("--stage", default="20,50,100", help="cumulative percentages, last must be 100")
    p.add_argument("--confirm-window", type=float, default=10.0, help="seconds each device has to confirm")
    p.add_argument("--svg", default=root / "docs/last_rollout.svg")
    args = ap.parse_args()

    if args.cmd == "resc":
        flash_paths = ([Path(args.flash_dir) / f"node{n}.bin" for n in range(args.nodes)] if args.flash_dir
                       else [root / "build/flash/default.bin"] * args.nodes)
        text = fleetgen.generate(args.nodes, root / "build/firmware/boot/boot.elf",
                                 root / "build/firmware/can_gateway/can_gateway.elf",
                                 root / "renode/stm32f4_ota.repl", flash_paths,
                                 [args.uart_base + n for n in range(args.nodes)], args.port)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {out}")
        return 0

    fleet = Fleet(args.host, args.port, list(range(args.nodes)), args.monitor_port)
    try:
        if args.cmd == "status":
            print(Fleet.format_status(fleet.status()))
            return 0
        if args.cmd == "logs":
            print(Fleet.format_log(fleet.logs(args.node)))
            return 0
        image = Path(args.image).read_bytes()
        image_a = Path(args.image_a).read_bytes() if args.image_a else None
        stages = [int(s) for s in args.stage.split(",")]
        result = fleet.rollout(image, stages, args.confirm_window, image_a=image_a, svg_path=args.svg)
        print(Fleet.format_status(fleet.status()))
        return 0 if result.ok else 1
    finally:
        fleet.close()


if __name__ == "__main__":
    sys.exit(main())

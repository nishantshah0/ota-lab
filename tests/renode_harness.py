"""
Drive a headless Renode instance from Python.

RenodeLab starts Renode with the monitor on a telnet port, loads
renode/ota_lab.resc with the firmware paths and UART ports filled in, connects
to both UART sockets, and only then starts the emulation. Everything is plain
sockets, so it works on Linux, macOS and Windows.
"""
from __future__ import annotations

import os
import queue
import re
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]

_ANSI_RE = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")
_TELNET_IAC = 255


def free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, timeout: float) -> socket.socket:
    """Connect to 127.0.0.1:port, retrying until timeout. Returns the socket."""
    deadline = time.monotonic() + timeout
    last_err: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=2.0)
            s.settimeout(None)
            return s
        except OSError as e:  # noqa: PERF203
            last_err = e
            time.sleep(0.2)
    raise TimeoutError(f"port {port} not accepting connections: {last_err}")


def _strip_telnet(data: bytes) -> bytes:
    """Drop telnet IAC negotiation sequences (IAC <cmd> <opt>)."""
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i] == _TELNET_IAC and i + 2 < len(data):
            i += 3
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


@dataclass
class Line:
    text: str
    t: float  # time.monotonic() when the line completed


class LineReader:
    """Background reader that splits a socket stream into lines.

    ``alive`` is polled while waiting so that a dead emulator surfaces as a
    clear error right away instead of a generic timeout.
    """

    def __init__(self, sock: socket.socket, name: str, alive: Optional[Callable[[], None]] = None):
        self.sock = sock
        self.name = name
        self.alive = alive
        self.lines: "queue.Queue[Line]" = queue.Queue()
        self.history: list[Line] = []
        self._buf = b""
        self._stop = False
        self._thread = threading.Thread(target=self._run, name=f"reader-{name}", daemon=True)
        self._thread.start()

    def _run(self) -> None:
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
                line = Line(raw.decode("utf-8", "replace").rstrip("\r"), time.monotonic())
                self.history.append(line)
                self.lines.put(line)

    def readline(self, timeout: float) -> Line:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{self.name}: no line within {timeout}s")
            try:
                return self.lines.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                if self.alive is not None:
                    self.alive()

    def expect(self, pattern: str, timeout: float) -> tuple[re.Match, Line]:
        """Consume lines until one matches pattern (re.search)."""
        rx = re.compile(pattern)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"{self.name}: pattern {pattern!r} not seen within {timeout}s; "
                    f"last lines: {[l.text for l in self.history[-10:]]}"
                )
            line = self.readline(remaining)
            m = rx.search(line.text)
            if m:
                return m, line

    def write(self, data: bytes) -> None:
        self.sock.sendall(data)

    def close(self) -> None:
        self._stop = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class Monitor:
    """Minimal client for Renode's telnet monitor."""

    _PROMPT_RE = re.compile(r"(\([^)\r\n]*\) )$")

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.sock.settimeout(0.5)
        self._buf = b""
        self.last_prompt = ""

    def _read_until_prompt(self, timeout: float) -> str:
        """Return everything received up to (excluding) the next prompt."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                chunk = b""
            except OSError:
                break
            if chunk:
                self._buf += _strip_telnet(chunk)
            text = _ANSI_RE.sub(b"", self._buf).decode("utf-8", "replace")
            m = self._PROMPT_RE.search(text)
            if m:
                self._buf = b""
                self.last_prompt = m.group(1)
                return text[: m.start()]
        text = _ANSI_RE.sub(b"", self._buf).decode("utf-8", "replace")
        self._buf = b""
        return text

    def wait_ready(self, timeout: float = 30.0) -> None:
        """Nudge the monitor with an empty line and wait for its prompt."""
        self.sock.sendall(b"\n")
        self._read_until_prompt(timeout)

    def command(self, cmd: str, timeout: float = 30.0) -> str:
        self.sock.sendall(cmd.encode() + b"\n")
        return self._read_until_prompt(timeout)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


@dataclass
class RenodeLab:
    dut_elf: Path
    gateway_elf: Path
    log_dir: Path
    renode: str = field(default_factory=lambda: os.environ.get("RENODE", "renode"))
    startup_timeout: float = 90.0

    proc: Optional[subprocess.Popen] = None
    monitor: Optional[Monitor] = None
    dut_uart: Optional[LineReader] = None
    gw_uart: Optional[LineReader] = None
    started_at: float = 0.0

    def __post_init__(self) -> None:
        self.monitor_port = free_tcp_port()
        self.dut_uart_port = free_tcp_port()
        self.gw_uart_port = free_tcp_port()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.renode_log = self.log_dir / "renode.log"
        self.stdout_log = self.log_dir / "renode-stdout.log"
        self.script = self.log_dir / "lab.resc"

    def _write_script(self) -> None:
        lab = (REPO_ROOT / "renode" / "ota_lab.resc").as_posix()
        self.script.write_text(
            "\n".join(
                [
                    f"$dut_bin=@{self.dut_elf.resolve().as_posix()}",
                    f"$gw_bin=@{self.gateway_elf.resolve().as_posix()}",
                    f"$dut_uart_port={self.dut_uart_port}",
                    f"$gw_uart_port={self.gw_uart_port}",
                    f"logFile @{self.renode_log.as_posix()}",
                    f"include @{lab}",
                    "",
                ]
            )
        )

    def start(self) -> "RenodeLab":
        exe = shutil.which(self.renode) or self.renode
        self._write_script()
        cmd = [exe, "--disable-gui", "-P", str(self.monitor_port), self.script.as_posix()]
        self._stdout = open(self.stdout_log, "wb")
        self.proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=self._stdout,
            stderr=subprocess.STDOUT,
        )
        try:
            self.monitor = Monitor(wait_for_port(self.monitor_port, self.startup_timeout))
            self.monitor.wait_ready(10.0)
            # Both machines exist once the script has run; the socket terminals
            # are created at the top of it, so they may be up slightly earlier.
            self.dut_uart = LineReader(wait_for_port(self.dut_uart_port, self.startup_timeout), "dut-uart", self.check_alive)
            self.gw_uart = LineReader(wait_for_port(self.gw_uart_port, self.startup_timeout), "gw-uart", self.check_alive)
            self._wait_for_script()
            self.started_at = time.monotonic()
            self.monitor.command("start")
        except Exception:
            self.stop()
            raise
        return self

    def _wait_for_script(self) -> None:
        """Poll until the script has created both machines.

        Selecting a machine changes the prompt from "(monitor) " to
        "(<name>) ", which is a reliable signal that it exists.
        """
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            self.monitor.command('mach set "gateway"', timeout=10.0)
            if self.monitor.last_prompt == "(gateway) ":
                self.monitor.command('mach set "dut"', timeout=10.0)
                if self.monitor.last_prompt == "(dut) ":
                    return
            time.sleep(0.5)
        raise TimeoutError("Renode script did not create both machines; see " + str(self.stdout_log))

    def check_alive(self) -> None:
        """Raise with the tail of Renode's output if the process has exited."""
        if self.proc is not None and self.proc.poll() is not None:
            self._stdout.flush()
            tail = self.stdout_log.read_text(errors="replace").splitlines()[-25:]
            raise RuntimeError(
                f"Renode exited with code {self.proc.returncode}; last output:\n" + "\n".join(tail)
            )

    def pause(self) -> None:
        self.monitor.command("pause")

    def resume(self) -> None:
        self.monitor.command("start")

    def virtual_time_s(self, machine: str = "dut") -> float:
        """Elapsed virtual time of a machine, in seconds, as reported by Renode."""
        self.monitor.command(f'mach set "{machine}"')
        out = self.monitor.command("machine ElapsedVirtualTime")
        m = re.search(r"Elapsed Virtual Time: (\d+):(\d+):(\d+)\.(\d+)", out)
        if not m:
            raise RuntimeError(f"could not parse virtual time from: {out!r}")
        h, mi, s, frac = m.groups()
        return int(h) * 3600 + int(mi) * 60 + int(s) + int(frac) / 10 ** len(frac)

    def led_state(self) -> bool:
        self.monitor.command('mach set "dut"')
        out = self.monitor.command("sysbus.gpioPortD.UserLED State")
        m = re.search(r"\b(True|False)\b", out)
        if not m:
            raise RuntimeError(f"could not parse LED state from monitor output: {out!r}")
        return m.group(1) == "True"

    def stop(self) -> None:
        for r in (self.dut_uart, self.gw_uart):
            if r:
                r.close()
        if self.monitor:
            try:
                self.monitor.sock.sendall(b"quit\n")
            except OSError:
                pass
            self.monitor.close()
        if self.proc:
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if getattr(self, "_stdout", None):
            self._stdout.close()

    # Convenience for the CAN gateway protocol.
    def can_send(self, can_id: int, data: bytes, extended: bool = False, timeout: float = 10.0) -> None:
        prefix = "T" if extended else "t"
        width = 8 if extended else 3
        line = f"{prefix}{can_id:0{width}X}{len(data)}{data.hex().upper()}\r"
        self.gw_uart.write(line.encode())
        m, _ = self.gw_uart.expect(r"^(OK|ERR)$", timeout)
        if m.group(1) != "OK":
            raise RuntimeError(f"gateway rejected frame {line!r}")

    def can_recv(self, timeout: float = 10.0) -> tuple[int, bool, bytes]:
        m, _ = self.gw_uart.expect(r"^([tT])([0-9A-F]{3}|[0-9A-F]{8})([0-8])([0-9A-F]*)$", timeout)
        extended = m.group(1) == "T"
        can_id = int(m.group(2), 16)
        dlc = int(m.group(3))
        payload = bytes.fromhex(m.group(4))
        if len(payload) != dlc:
            raise RuntimeError(f"gateway frame length mismatch: {m.group(0)!r}")
        return can_id, extended, payload

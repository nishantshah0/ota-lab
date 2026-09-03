import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from renode_harness import REPO_ROOT, RenodeLab

sys.path.insert(0, str(REPO_ROOT / "tools"))
import bootjournal  # noqa: E402
import flashimage  # noqa: E402
import otaimg  # noqa: E402

BUILD_DIR = Path(os.environ.get("BUILD_DIR", REPO_ROOT / "build"))
LOG_ROOT = REPO_ROOT / "test-logs"
KEY_PATH = REPO_ROOT / "keys" / "dev_ed25519.key"


def _build_firmware() -> None:
    generator = ["-G", "Ninja"] if shutil.which("ninja") else []
    subprocess.run(["cmake", "-S", str(REPO_ROOT), "-B", str(BUILD_DIR), *generator], check=True)
    subprocess.run(["cmake", "--build", str(BUILD_DIR)], check=True)


class Artifacts:
    """Paths to the build outputs the tests consume."""

    def __init__(self, build: Path):
        self.build = build
        self.boot_elf = build / "firmware/boot/boot.elf"
        self.boot_bin = build / "firmware/boot/boot.bin"
        self.safe_bin = build / "firmware/safe/safe.bin"
        self.gateway_elf = build / "firmware/can_gateway/can_gateway.elf"

    def app_body(self, variant: str, slot: str) -> bytes:
        return (self.build / f"firmware/app/app_{variant}_{slot}.bin").read_bytes()

    def required(self) -> list[Path]:
        return [self.boot_elf, self.boot_bin, self.safe_bin, self.gateway_elf,
                self.build / "firmware/app/app_good_A.bin"]


@pytest.fixture(scope="session")
def artifacts() -> Artifacts:
    art = Artifacts(BUILD_DIR)
    if not all(p.exists() for p in art.required()):
        _build_firmware()
    missing = [p for p in art.required() if not p.exists()]
    assert not missing, f"missing build outputs: {missing}"
    return art


class FlashBuilder:
    """Compose a flash image for one scenario.

    Slot contents are described by (variant, kind) where kind is one of
    'good', 'corrupt_crc', 'bad_signature', 'wrong_slot', 'corrupt_body',
    'garbage' or 'empty'.
    """

    VERSION = (0, 3, 0)

    def __init__(self, art: Artifacts):
        self.art = art
        self.key = otaimg.load_private_key(KEY_PATH)
        self.img = flashimage.FlashImage()
        self.img.place_file(flashimage.BOOT_ADDR, art.boot_bin)
        self.img.place_file(flashimage.SAFE_ADDR, art.safe_bin)

    def image(self, slot: str, variant: str = "good", kind: str = "good", version=None) -> bytes:
        """A complete signed image (header + body) for a slot, as a file would hold it."""
        body = self.art.app_body(variant, slot)
        flags = {
            "good": {},
            "corrupt_crc": {"corrupt_crc": True},
            "bad_signature": {"bad_signature": True},
            "wrong_slot": {"wrong_slot": True},
            "corrupt_body": {"corrupt_body": True},
        }[kind]
        return otaimg.build_image(body, version or self.VERSION, otaimg.slot_index(slot), self.key, **flags)

    def slot(self, slot: str, variant: str = "good", kind: str = "good", version=None) -> "FlashBuilder":
        addr = flashimage.SLOT_A if slot == "A" else flashimage.SLOT_B
        if kind == "empty":
            self.img.erase(addr, flashimage.SLOT_SIZE)
        elif kind == "garbage":
            self.img.place(addr, bytes((i * 7 + 3) & 0xFF for i in range(4096)))
        else:
            self.img.place(addr, self.image(slot, variant, kind, version))
        return self

    def journal(self, *records: bytes, bank: int = 0) -> "FlashBuilder":
        addr = flashimage.JOURNAL_BANK0 if bank == 0 else flashimage.JOURNAL_BANK1
        self.img.place(addr, b"".join(records))
        return self

    def state(self, active: int, pending: int = bootjournal.SLOT_NONE,
              attempts: int = 0, confirmed: int = 0, seq: int = 1) -> "FlashBuilder":
        """Convenience: a single valid journal record."""
        return self.journal(bootjournal.pack_record(seq, active, pending, attempts, confirmed))

    def bootlog(self, raw: bytes) -> "FlashBuilder":
        self.img.place(flashimage.BOOTLOG_ADDR, raw)
        return self

    def raw(self, addr: int, data: bytes) -> "FlashBuilder":
        self.img.place(addr, data)
        return self

    def build(self) -> flashimage.FlashImage:
        return self.img


@pytest.fixture
def flash(artifacts) -> FlashBuilder:
    return FlashBuilder(artifacts)


@pytest.fixture
def lab_factory(artifacts, request):
    """Start a RenodeLab for a given flash image; stops it at teardown."""
    labs: list[RenodeLab] = []
    log_dir = LOG_ROOT / re.sub(r"[^\w.-]+", "_", request.node.name)
    counter = {"n": 0}

    def start(image: flashimage.FlashImage, autostart: bool = True) -> RenodeLab:
        counter["n"] += 1
        run_dir = log_dir if counter["n"] == 1 else log_dir / f"run{counter['n']}"
        run_dir.mkdir(parents=True, exist_ok=True)
        flash_path = image.write(run_dir / "flash.bin")
        lab = RenodeLab(boot_elf=artifacts.boot_elf, flash_image=flash_path,
                        gateway_elf=artifacts.gateway_elf, log_dir=run_dir)
        labs.append(lab)
        return lab.start(autostart=autostart)

    yield start

    for lab in labs:
        lab.stop()
        for name, reader in (("dut-uart.txt", lab.dut_uart), ("gw-uart.txt", lab.gw_uart)):
            if reader is not None:
                (lab.log_dir / name).write_text("\n".join(l.text for l in reader.history) + "\n")


@pytest.fixture
def lab(flash, lab_factory):
    """Default scenario: good image in slot A, empty B, erased journal."""
    return lab_factory(flash.slot("A").build())

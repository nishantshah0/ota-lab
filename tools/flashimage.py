"""
Assemble a complete flash image for the emulator from its parts.

The image covers the first 640 KiB of flash (sectors 0..8), starts out fully
erased (0xFF) and takes regions by address. Renode loads it in one go with
'sysbus LoadBinary', which stands in for a flash programmer.
"""
from __future__ import annotations

from pathlib import Path

FLASH_BASE = 0x08000000
IMAGE_SIZE = 0xA0000  # sectors 0..8

BOOT_ADDR = 0x08000000
JOURNAL_BANK0 = 0x08008000
JOURNAL_BANK1 = 0x0800C000
JOURNAL_BANK_SIZE = 0x4000
SAFE_ADDR = 0x08010000
SLOT_A = 0x08020000
SLOT_B = 0x08040000
SLOT_SIZE = 0x20000
BOOTLOG_ADDR = 0x08060000
BOOTLOG_SIZE = 0x20000
PROGRESS_ADDR = 0x08080000
PROGRESS_SIZE = 0x20000


class FlashImage:
    def __init__(self) -> None:
        self.data = bytearray(b"\xff" * IMAGE_SIZE)

    def place(self, addr: int, blob: bytes) -> "FlashImage":
        off = addr - FLASH_BASE
        if off < 0 or off + len(blob) > IMAGE_SIZE:
            raise ValueError(f"region 0x{addr:08X}+{len(blob)} outside the image")
        self.data[off:off + len(blob)] = blob
        return self

    def place_file(self, addr: int, path) -> "FlashImage":
        return self.place(addr, Path(path).read_bytes())

    def erase(self, addr: int, size: int) -> "FlashImage":
        return self.place(addr, b"\xff" * size)

    def read(self, addr: int, size: int) -> bytes:
        off = addr - FLASH_BASE
        return bytes(self.data[off:off + size])

    def to_bytes(self) -> bytes:
        return bytes(self.data)

    def write(self, path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.to_bytes())
        return p

    @classmethod
    def from_bytes(cls, data: bytes) -> "FlashImage":
        img = cls()
        img.data[: len(data)] = data[:IMAGE_SIZE]
        return img

"""
Signed image format shared by sign_image.py, the tests and the bootloader.
Mirrors firmware/common/image.h; keep the two in sync.

Layout (little endian, 512 bytes total):
  0   u32  magic          "OTA2"
  4   u16  header_version 1
  6   u16  header_size    512
  8   u32  image_size     body length, multiple of 4
  12  u8   ver_major
  13  u8   ver_minor
  14  u8   ver_patch
  15  u8   flags
  16  u8   target_slot    0 = A, 1 = B
  17  u8[3] reserved0
  20  u32  body_crc32
  24  u32  load_address   slot base + 512
  28  u32  reserved1
  32  u8[64] signature    Ed25519 over header[0:32] + SHA-512(body)
  96  ...  0xFF padding

Signing over the SHA-512 of the body instead of the body itself lets the
bootloader verify a 128 KiB image with a streaming hash and a 96 byte
signature check, with no RAM copy of the image.
"""
from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

MAGIC = 0x3241544F
HEADER_VERSION = 1
HEADER_SIZE = 512
SIGNED_PREFIX = 32

SLOT_A = 0
SLOT_B = 1
SLOT_BASE = {SLOT_A: 0x08020000, SLOT_B: 0x08040000}
SLOT_SIZE = 0x20000

_PREFIX = struct.Struct("<IHHIBBBBB3xIII")


def slot_index(name: str | int) -> int:
    if isinstance(name, int):
        return name
    return {"A": SLOT_A, "B": SLOT_B, "a": SLOT_A, "b": SLOT_B}[name]


def parse_version(text: str) -> tuple[int, int, int]:
    parts = text.split(".")
    if len(parts) != 3:
        raise ValueError(f"version must be major.minor.patch, got {text!r}")
    major, minor, patch = (int(p) for p in parts)
    for v in (major, minor, patch):
        if not 0 <= v <= 255:
            raise ValueError("version components must fit in a byte")
    return major, minor, patch


def load_private_key(path) -> Ed25519PrivateKey:
    seed = bytes.fromhex(open(path, "r", encoding="utf-8").read().strip())
    return Ed25519PrivateKey.from_private_bytes(seed)


def load_public_key(path) -> Ed25519PublicKey:
    raw = bytes.fromhex(open(path, "r", encoding="utf-8").read().strip())
    return Ed25519PublicKey.from_public_bytes(raw)


def signed_message(prefix: bytes, body: bytes) -> bytes:
    return prefix + hashlib.sha512(body).digest()


def pad_body(body: bytes) -> bytes:
    if len(body) % 4:
        body += b"\xff" * (4 - len(body) % 4)
    return body


def build_image(
    body: bytes,
    version: tuple[int, int, int],
    slot: int,
    key: Ed25519PrivateKey,
    *,
    corrupt_crc: bool = False,
    bad_signature: bool = False,
    wrong_slot: bool = False,
    corrupt_body: bool = False,
) -> bytes:
    """Return header + body ready to be placed at the slot base."""
    body = pad_body(body)
    if len(body) > SLOT_SIZE - HEADER_SIZE:
        raise ValueError(f"body of {len(body)} bytes does not fit a slot")

    declared_slot = (SLOT_B if slot == SLOT_A else SLOT_A) if wrong_slot else slot
    load_address = SLOT_BASE[slot] + HEADER_SIZE
    crc = zlib.crc32(body) & 0xFFFFFFFF
    if corrupt_crc:
        crc ^= 0x00000001

    prefix = _PREFIX.pack(
        MAGIC, HEADER_VERSION, HEADER_SIZE, len(body),
        version[0], version[1], version[2], 0,
        declared_slot,
        crc, load_address, 0,
    )
    assert len(prefix) == SIGNED_PREFIX

    signature = key.sign(signed_message(prefix, body))
    if bad_signature:
        signature = bytes([signature[0] ^ 0x01]) + signature[1:]

    header = prefix + signature
    header += b"\xff" * (HEADER_SIZE - len(header))

    if corrupt_body:
        # Flip one byte in the middle of the body after signing and CRC-ing.
        mid = len(body) // 2
        body = body[:mid] + bytes([body[mid] ^ 0xFF]) + body[mid + 1:]
    return header + body


@dataclass
class Header:
    magic: int
    header_version: int
    header_size: int
    image_size: int
    version: tuple[int, int, int]
    flags: int
    target_slot: int
    body_crc32: int
    load_address: int
    signature: bytes

    @property
    def version_str(self) -> str:
        return ".".join(str(v) for v in self.version)


def parse_header(data: bytes) -> Header:
    (magic, hver, hsize, isize, maj, mi, pa, flags, slot, crc, load, _res) = _PREFIX.unpack(
        data[:SIGNED_PREFIX]
    )
    return Header(magic, hver, hsize, isize, (maj, mi, pa), flags, slot, crc, load,
                  bytes(data[SIGNED_PREFIX:SIGNED_PREFIX + 64]))


def verify_image(image: bytes, pub: Ed25519PublicKey) -> str:
    """Mirror of the bootloader's validate_slot(); returns 'OK' or an error name."""
    h = parse_header(image)
    if h.magic != MAGIC:
        return "BAD_MAGIC"
    if h.header_version != HEADER_VERSION or h.header_size != HEADER_SIZE:
        return "BAD_HEADER"
    if h.image_size == 0 or h.image_size > SLOT_SIZE - HEADER_SIZE or h.image_size % 4:
        return "BAD_SIZE"
    body = image[HEADER_SIZE:HEADER_SIZE + h.image_size]
    if (zlib.crc32(body) & 0xFFFFFFFF) != h.body_crc32:
        return "BAD_CRC"
    try:
        pub.verify(h.signature, signed_message(image[:SIGNED_PREFIX], body))
    except Exception:  # noqa: BLE001
        return "BAD_SIGNATURE"
    return "OK"

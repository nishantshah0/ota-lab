#!/usr/bin/env python3
"""
Wrap a raw firmware body (.bin from objcopy, linked at slot base + 512) in a
signed OTA header.

  python tools/sign_image.py --key keys/dev_ed25519.key --slot A \
      --version 0.2.0 --in build/firmware/app/app_good_A.bin \
      --out build/firmware/app/app_good_A.signed.bin

Test knobs (each produces an image the bootloader must reject):
  --corrupt-crc     wrong body CRC
  --bad-signature   one bit flipped in the signature
  --wrong-slot      header claims the other slot
  --corrupt-body    one byte flipped in the body after signing
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import otaimg  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", required=True)
    ap.add_argument("--slot", required=True, choices=["A", "B"])
    ap.add_argument("--version", required=True)
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--corrupt-crc", action="store_true")
    ap.add_argument("--bad-signature", action="store_true")
    ap.add_argument("--wrong-slot", action="store_true")
    ap.add_argument("--corrupt-body", action="store_true")
    args = ap.parse_args()

    body = Path(args.inp).read_bytes()
    key = otaimg.load_private_key(args.key)
    image = otaimg.build_image(
        body,
        otaimg.parse_version(args.version),
        otaimg.slot_index(args.slot),
        key,
        corrupt_crc=args.corrupt_crc,
        bad_signature=args.bad_signature,
        wrong_slot=args.wrong_slot,
        corrupt_body=args.corrupt_body,
    )
    Path(args.out).write_bytes(image)
    h = otaimg.parse_header(image)
    status = otaimg.verify_image(image, key.public_key())
    print(f"{Path(args.out).name}: slot {args.slot} v{h.version_str} body {h.image_size} B "
          f"crc 0x{h.body_crc32:08X} load 0x{h.load_address:08X} self-check {status}")


if __name__ == "__main__":
    main()

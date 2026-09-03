#!/usr/bin/env python3
"""
Build a flash image from bootloader, safe-mode image and signed slot images.

  python tools/mkflash.py --boot boot.bin --safe safe.bin \
      --slot-a app_good_A.signed.bin [--slot-b app_good_B.signed.bin] --out flash.bin

Journal and boot log regions are left erased unless --journal-bank0 /
--journal-bank1 / --bootlog point at raw region contents (the tests use the
Python API in flashimage.py and bootjournal.py directly instead).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from flashimage import (  # noqa: E402
    BOOT_ADDR, BOOTLOG_ADDR, JOURNAL_BANK0, JOURNAL_BANK1, SAFE_ADDR, SLOT_A, SLOT_B, FlashImage,
)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boot", required=True)
    ap.add_argument("--safe", required=True)
    ap.add_argument("--slot-a")
    ap.add_argument("--slot-b")
    ap.add_argument("--journal-bank0")
    ap.add_argument("--journal-bank1")
    ap.add_argument("--bootlog")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    img = FlashImage()
    img.place_file(BOOT_ADDR, args.boot)
    img.place_file(SAFE_ADDR, args.safe)
    for addr, path in ((SLOT_A, args.slot_a), (SLOT_B, args.slot_b),
                       (JOURNAL_BANK0, args.journal_bank0), (JOURNAL_BANK1, args.journal_bank1),
                       (BOOTLOG_ADDR, args.bootlog)):
        if path:
            img.place_file(addr, path)
    out = img.write(args.out)
    print(f"wrote {out} ({len(img.data)} bytes)")


if __name__ == "__main__":
    main()

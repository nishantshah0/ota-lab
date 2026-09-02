import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from renode_harness import REPO_ROOT, RenodeLab

BUILD_DIR = Path(os.environ.get("BUILD_DIR", REPO_ROOT / "build"))
DUT_ELF = BUILD_DIR / "firmware" / "app" / "ota_app.elf"
GW_ELF = BUILD_DIR / "firmware" / "can_gateway" / "can_gateway.elf"
LOG_ROOT = REPO_ROOT / "test-logs"


def _build_firmware() -> None:
    generator = ["-G", "Ninja"] if shutil.which("ninja") else []
    subprocess.run(["cmake", "-S", str(REPO_ROOT), "-B", str(BUILD_DIR), *generator], check=True)
    subprocess.run(["cmake", "--build", str(BUILD_DIR)], check=True)


@pytest.fixture(scope="session")
def firmware() -> dict:
    """Build the firmware once per session if the ELFs are missing."""
    if not (DUT_ELF.exists() and GW_ELF.exists()):
        _build_firmware()
    assert DUT_ELF.exists(), f"missing {DUT_ELF}"
    assert GW_ELF.exists(), f"missing {GW_ELF}"
    return {"dut": DUT_ELF, "gateway": GW_ELF}


@pytest.fixture
def lab(firmware, request):
    """A fresh Renode instance per test, so every test sees a cold boot."""
    # Parametrised ids can contain characters that are awkward in paths.
    log_dir = LOG_ROOT / re.sub(r"[^\w.-]+", "_", request.node.name)
    lab = RenodeLab(dut_elf=firmware["dut"], gateway_elf=firmware["gateway"], log_dir=log_dir)
    lab.start()
    try:
        yield lab
    finally:
        lab.stop()
        # Keep the UART transcript next to the Renode log for debugging.
        for name, reader in (("dut-uart.txt", lab.dut_uart), ("gw-uart.txt", lab.gw_uart)):
            if reader is not None:
                (log_dir / name).write_text("\n".join(l.text for l in reader.history) + "\n")

"""
Phase 5: fleet of five devices on one bus, staged rollout driven by
tools/fleet.py. Timelines use Renode virtual time through the monitor.
"""
import re
import sys

import pytest

from renode_harness import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
from fleet_harness import FleetLab  # noqa: E402

NODES = 5
STAGES = [20, 50, 100]
NEW_VERSION = (0, 3, 1)
OLD_VERSION = "0.3.0"


@pytest.fixture
def fleet_factory(artifacts, request):
    labs = []
    log_dir = REPO_ROOT / "test-logs" / re.sub(r"[^\w.-]+", "_", request.node.name)

    def start(images):
        lab = FleetLab(artifacts, images, log_dir).start()
        labs.append(lab)
        lab.wait_all_ready()
        return lab

    yield start
    for lab in labs:
        lab.stop()


def good_node(flash):
    return flash.slot("A").build()


def versions(rows):
    return {r["node"]: (r["version"] if r.get("reachable") else None) for r in rows}


def test_staged_rollout_happy_path(artifacts, fleet_factory, request):
    from conftest import FlashBuilder
    images = [good_node(FlashBuilder(artifacts)) for _ in range(NODES)]
    lab = fleet_factory(images)
    for n in range(NODES):
        assert any(l.text == f"node: {n}" for l in lab.dut_uarts[n].history), f"node {n} banner"

    new_image = FlashBuilder(artifacts).image("B", version=NEW_VERSION)
    before = lab.fleet.status()
    assert all(r["version"] == OLD_VERSION and r["running"] == "A" for r in before)

    svg = lab.log_dir / "rollout.svg"
    result = lab.fleet.rollout(new_image, STAGES, confirm_window=10.0, svg_path=svg)
    assert result.ok, result.report
    assert result.stages == [[0], [1, 2], [3, 4]]
    assert result.completed_stages == 3 and not result.failures

    after = lab.fleet.status()
    assert versions(after) == {n: "0.3.1" for n in range(NODES)}
    assert all(r["running"] == "B" and r["active"] == "B" for r in after)
    for n in range(NODES):
        assert any(l.text.startswith("confirm: written") for l in lab.dut_uarts[n].history), f"node {n} confirm"
        log = lab.fleet.logs(n)
        assert [e["reason"] for e in log] == ["ACTIVE", "PENDING_TRIAL"]

    text = svg.read_text()
    assert text.startswith("<svg") and all(f"node {n}" in text for n in range(NODES))
    assert "confirm" in text and result.clock_label.startswith("Renode virtual")
    # Every event lies inside the rollout window on the virtual clock.
    t_end = lab.virtual_time_s()
    assert all(0 <= e.t0 <= e.t1 <= t_end for e in result.events)
    request.node.user_properties.append(("rollout_virtual_s", round(result.stage_times[-1][1] - result.stage_times[0][0], 2)))


def test_rollout_halts_on_corrupted_device(artifacts, fleet_factory):
    from conftest import FlashBuilder
    images = [good_node(FlashBuilder(artifacts)) for _ in range(NODES)]
    # Node 2 (stage 2) has a corrupted running image and nothing in B: it
    # boots into safe mode, which has no CAN update path.
    images[2] = FlashBuilder(artifacts).slot("A", kind="bad_signature").build()
    lab = fleet_factory(images)
    assert any(l.text == "no valid image in slot A or B, entering safe mode" for l in lab.dut_uarts[2].history)

    before = lab.fleet.status()
    assert not before[2]["reachable"] and all(r["reachable"] for i, r in enumerate(before) if i != 2)

    new_image = FlashBuilder(artifacts).image("B", version=NEW_VERSION)
    result = lab.fleet.rollout(new_image, STAGES, confirm_window=10.0, svg_path=lab.log_dir / "rollout.svg")
    assert not result.ok
    assert result.completed_stages == 1
    assert [n for n, _ in result.failures] == [2]
    assert "no INFO reply" in result.failures[0][1]
    assert any("HALT at stage 2" in line and "node 2" in line for line in result.report)

    after = versions(lab.fleet.status())
    assert after[0] == "0.3.1"                      # earlier stage: updated
    assert after[2] is None                         # the corrupted device
    assert after[3] == OLD_VERSION and after[4] == OLD_VERSION   # later stage untouched
    assert after[1] in (OLD_VERSION, "0.3.1")       # same stage as the failure, ran concurrently


def test_rollout_halts_when_a_device_never_confirms(artifacts, fleet_factory):
    from conftest import FlashBuilder
    images = [good_node(FlashBuilder(artifacts)) for _ in range(NODES)]
    lab = fleet_factory(images)

    new_image = FlashBuilder(artifacts).image("B", version=NEW_VERSION)
    bad_image = FlashBuilder(artifacts).image("B", variant="noconfirm", version=NEW_VERSION)
    result = lab.fleet.rollout(new_image, STAGES, confirm_window=10.0,
                               image_overrides={1: bad_image}, svg_path=lab.log_dir / "rollout.svg")
    assert not result.ok
    assert result.completed_stages == 1
    assert [n for n, _ in result.failures] == [1]
    assert "reverted" in result.failures[0][1]
    assert any("HALT at stage 2" in line and "node 1" in line for line in result.report)

    after = lab.fleet.status()
    by = {r["node"]: r for r in after}
    assert by[1]["version"] == OLD_VERSION and by[1]["running"] == "A" and by[1]["last_reason"] == "ROLLBACK"
    assert by[0]["version"] == "0.3.1"
    assert by[3]["version"] == OLD_VERSION and by[4]["version"] == OLD_VERSION
    reasons = [e["reason"] for e in lab.fleet.logs(1)]
    assert reasons == ["ACTIVE", "PENDING_TRIAL", "PENDING_TRIAL", "PENDING_TRIAL", "ROLLBACK"]
    assert any("revert" in line for line in open(lab.log_dir / "rollout.svg").read().splitlines())

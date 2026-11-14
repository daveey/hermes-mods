"""Flat executable directories retain exact-file and test/code safety."""
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("flat_bin_guard", Path(__file__).with_name("guard.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

@pytest.mark.parametrize("directory", ["home/bin", "hosts/example/bin"])
def test_distinct_flat_bin_files(directory):
    assert g._overlap(f"{directory}/tool", f"{directory}/other.json") == ""
    assert g.turn_blocks([f"{directory}/tool"], [f"{directory}/other.json"]) is None

@pytest.mark.parametrize("path", ["hermes-sync", "home/bin/tool", "hosts/example/bin/tool"])
def test_same_file(path):
    assert g._overlap(path, path) == "area"

@pytest.mark.parametrize("directory", ["home/bin", "hosts/example/bin"])
def test_coupling_falls_through(directory):
    script = f"{directory}/example-tool"
    for test in [f"{directory}/test_example_tool.py", "tests/test_example_tool.py"]:
        assert g._overlap(script, test) == "test-code"
        assert g._overlap(test, script) == "test-code"

@pytest.mark.parametrize("left,right", [
    ("hosts/example/bin/sub/a", "hosts/example/bin/sub/b"),
    ("hosts/example/bin/tool", "hosts/example/bin/sub/a"),
    ("home/bin/sub/a", "home/bin/sub/b"),
    ("home/scripts/a", "home/scripts/b"),
])
def test_other_area_rules_retained(left, right):
    assert g._overlap(left, right) == "area"
    assert g._overlap(right, left) == "area"

def test_global_ci_retained():
    assert g._overlap("home/bin/tool", ".github/workflows/check.yml") == "ci"

def test_disjoint_land_turn_lists():
    holder = ["home/bin/hermes-core-wt-land", "tests/test_core_wt_land_lane.py", "tests/test_core_wt_ship_gate.py"]
    candidate = ["hermes-sync", "hermes-sync-pause", "home/bin/gateway-idle-restart",
                 "hosts/example-hub/bin/hub-core-poll", "hosts/example-hub/bin/hub-core-update",
                 "hosts/example-workers/bin/worker-core-update", "docs/core-update-safety.md",
                 "home/skills/example/references/runtime.md", "tests/test_core_update_pause.py"]
    assert g.turn_blocks(holder, candidate) is None
    assert g.turn_blocks(candidate, holder) is None

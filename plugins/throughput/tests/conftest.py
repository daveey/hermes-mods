"""Load the plugin package under a fixed name (its directory is not on sys.path as a package)."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "throughput_under_test"

if NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = mod
    spec.loader.exec_module(mod)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_hub_link(tmp_path, monkeypatch):
    """Never read this machine's hermes-hub replica: the throttle's fleet level comes from a test's own fixture."""
    monkeypatch.setenv("HERMES_HUB_STATE", str(tmp_path / "no-hub-link"))
    # The throttle classifies a call by these (card run vs chat): a test run inside a card must not inherit them.
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_WORKER_TASK", raising=False)
    from throughput_under_test import runway, slotgate
    # Core-backed runs resolve pacing via kanban_home(), which is this host's live file. Keep it in the test root
    # so a budget-publish host cannot rewrite the fixture level during sampler._loop.
    monkeypatch.setattr(runway, "pacing_path", lambda root: Path(root) / "llm_pacing.json")
    # Core's active_card_runs() is this machine's live count. Without this, Gate.start_gate takes the slot-gate
    # path (token_slots) instead of the rate-rule tests' token_throttle / None.
    monkeypatch.setattr(slotgate, "active_runs", lambda: None)

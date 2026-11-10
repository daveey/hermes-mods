"""A defect with no session (a cron run) must not print a `@session:default/` placeholder in the brief:
kanban-honest-done read that empty link as an origin chat named `default` and refused the fix card's
completion (cron-b48bd46e2a)."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("coach_self_heal_no_session_test", ROOT / "self_heal.py")
sh = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sh
spec.loader.exec_module(sh)


def test_no_session_prints_none_not_empty_link():
    brief = sh.investigation_brief({"fingerprint": "cron-12345678", "source": "cron"})
    assert "@session:default/" not in brief
    assert "session: none (no chat" in brief


def test_session_link_kept_when_present():
    brief = sh.investigation_brief({"fingerprint": "agent-12345678", "session_id": "20261025_233046_d48ade"})
    assert "@session:default/20261025_233046_d48ade" in brief

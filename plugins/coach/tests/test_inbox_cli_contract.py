"""The lane's generated commands must survive test-home pollution in a persistent terminal."""
import importlib.util
from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("coach_inbox_contract", ROOT / "self_heal.py")
sh = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sh
spec.loader.exec_module(sh)


def briefs():
    # Only the fix brief posts (the "Fixed:" result); proposals are auto-approved and never reach the Inbox.
    return [sh.fix_brief("desktop-12345678", {})]


def test_investigation_brief_runs_no_inbox_command():
    assert not re.findall(r"`([^`]*hermes inbox[^`]*)`", sh.investigation_brief({"fingerprint": "desktop-12345678"}))


def test_fix_brief_forbids_inbox_post_and_records_quietly():
    # Finished fixes are recorded under selfheal-results/, never posted: the brief only names the
    # command to forbid it, so no generated Inbox command exists to pin to the lane home.
    text = briefs()[0]
    assert re.findall(r"`([^`]*hermes inbox[^`]*)`", text) == ["hermes inbox post"]
    assert "do NOT run `hermes inbox post`" in text
    assert "event fixed" in text and "selfheal-results" in text

"""Every self-heal Kanban producer routes by class only: `class:deep` for code-writing cards, no pin for triage
cards; never a concrete model, a provider, or --pin-mandatory. Aux judge calls are not cards and are untouched."""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("coach_self_heal_class_routing_test", ROOT / "self_heal.py")
sh = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sh
spec.loader.exec_module(sh)

BASE = {"title": "t", "body": "b", "assignee": "default", "idempotency_key": "k", "max_runtime": 60,
        "created_by": "self-heal", "skills": [], "requires": [], "home_pin": False, "runs_on": []}


def test_constants_are_class_only():
    assert sh.MODEL == "class:deep" and sh.PROVIDER is None
    assert sh.TRIAGE_MODEL in (None, "class:standard") and sh.TRIAGE_PROVIDER is None


@pytest.mark.parametrize("model,provider", [
    ("claude-opus-5-5", "anthropic"), ("gpt-6.1-sol", None), ("claude-opus-5-5", None), (None, "anthropic"),
    ("anthropic/claude-opus-5-5", "openrouter")])
def test_argv_ignores_concrete_overrides(model, provider):
    argv = sh.kanban_argv({**BASE, "model": model, "provider": provider})
    assert "--model" not in argv and "--provider" not in argv and "--pin-mandatory" not in argv


@pytest.mark.parametrize("cls", ["class:deep", "class:standard"])
def test_argv_keeps_class_and_drops_provider(cls):
    argv = sh.kanban_argv({**BASE, "model": cls, "provider": "anthropic", "pin_mandatory": True})
    assert argv[argv.index("--model") + 1] == cls
    assert "--provider" not in argv and "--pin-mandatory" not in argv


def test_producer_constants_flow_into_argv():
    argv = sh.kanban_argv({**BASE, "model": sh.MODEL, "provider": sh.PROVIDER})
    assert argv[argv.index("--model") + 1] == "class:deep" and "--provider" not in argv
    argv = sh.kanban_argv({**BASE, "model": sh.TRIAGE_MODEL, "provider": sh.TRIAGE_PROVIDER})
    assert "--provider" not in argv and "--pin-mandatory" not in argv
    if sh.TRIAGE_MODEL:
        assert argv[argv.index("--model") + 1] == "class:standard"

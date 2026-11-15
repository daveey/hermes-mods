"""Exercise status_fresh's real sibling/config lookup, not the boolean branch."""
import importlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest


HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("status_fresh_config_rc", HERE / "reply_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


@pytest.fixture
def config_reader(monkeypatch):
    """Replace only core's I/O boundary; keep sibling loading and settings traversal real."""
    monkeypatch.delenv("COACH_STATUS_FRESH", raising=False)
    try:
        config = importlib.import_module("hermes_cli.config")
    except ImportError:  # Public no-core CI still exercises the real coach lookup.
        config = ModuleType("hermes_cli.config")
        monkeypatch.setitem(sys.modules, "hermes_cli.config", config)
    calls = []

    def install(settings):
        def read():
            calls.append("read")
            return {"plugins": {"entries": {"coach": {"settings": settings}}}}

        monkeypatch.setattr(config, "load_config_readonly", read, raising=False)
        return calls

    return install


@pytest.mark.parametrize("settings,expected", [
    ({"status_fresh": "off"}, False),
    ({"status_fresh": "on"}, True),
    ({}, True),
], ids=["off", "on", "absent-default"])
def test_config_switch_is_read(config_reader, settings, expected):
    calls = config_reader(settings)
    assert rc.status_fresh_enabled() is expected
    assert calls == ["read"]


@pytest.mark.parametrize("env,setting,expected", [
    ("on", "off", True),
    ("off", "on", False),
])
def test_environment_wins_without_reading_config(monkeypatch, config_reader, env, setting, expected):
    calls = config_reader({"status_fresh": setting})
    monkeypatch.setenv("COACH_STATUS_FRESH", env)
    assert rc.status_fresh_enabled() is expected
    assert calls == []

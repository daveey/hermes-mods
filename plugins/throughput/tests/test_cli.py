"""``hermes throttle``: parse, set and show the person's token cap from a terminal."""
import argparse
import json
import types

import pytest

from throughput_under_test import cli as C
from throughput_under_test import slotgate as S
from throughput_under_test import throttle as T


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(S, "active_runs", lambda: 3)
    return tmp_path


def test_amounts():
    assert C.parse_per_min("150k") == 150_000
    assert C.parse_per_min("1.5M") == 1_500_000
    assert C.parse_per_min("90000") == 90_000
    assert C.parse_per_min("0") == 0
    for word in ("off", "None", "auto", "budget"):
        assert C.parse_per_min(word) is None
    for bad in ("-5k", "lots", "5g", ""):
        with pytest.raises(ValueError):
            C.parse_per_min(bad)


def _run(*argv):
    parser = argparse.ArgumentParser(prog="hermes throttle")
    C.setup(parser)
    return C.handler(parser.parse_args(list(argv)))


def test_set_stores_a_manual_cap_in_tokens_per_second_and_status_shows_it(home, capsys):
    assert _run("set", "120k") == 0
    stored = json.loads(T.level_path(home).read_text())
    assert stored["mode"] == "manual" and stored["cap_per_s"] == pytest.approx(2000.0)
    out = capsys.readouterr().out
    assert "token cap set to 120.0k/min" in out and "cap in force:   120.0k/min" in out
    assert "your cap:       120.0k/min" in out
    assert "this gateway:   3 running, 2 allowed" in out  # no estimate yet: UNMEASURED_SLOTS
    seen = json.loads(T.seen_path(home).read_text())
    assert seen["set_at"] == stored["set_at"]


def test_set_off_removes_the_cap_and_a_bad_amount_changes_nothing(home, capsys):
    _run("set", "60k")
    assert _run("set", "off") == 0
    assert "cap_per_s" not in json.loads(T.level_path(home).read_text())
    before = T.level_path(home).read_text()
    assert _run("set", "lots") == 2
    assert T.level_path(home).read_text() == before
    assert "not a token amount" in capsys.readouterr().err


def test_plain_status_is_the_default(home, capsys):
    assert _run() == 0
    assert "cap in force:   none" in capsys.readouterr().out


def test_register_adds_the_throttle_cli_command(monkeypatch, tmp_path):
    import throughput_under_test as plugin
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    commands = []
    ctx = types.SimpleNamespace(register_cli_command=lambda name, help, setup, handler: commands.append(
        (name, setup, handler)))
    monkeypatch.setattr(plugin, "_register_throttle", lambda ctx: None)
    monkeypatch.setattr(plugin, "_advisory_lifecycle", lambda action: None)
    from throughput_under_test import sampler
    monkeypatch.setattr(sampler, "eligible_process", lambda *a, **k: False)
    plugin.register(ctx)
    assert [(n, s, h) for n, s, h in commands] == [("throttle", C.setup, C.handler)]

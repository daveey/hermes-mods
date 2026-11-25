"""The always-on backend must not serialize app startup behind projection warming."""
import threading
import time

import pytest


def test_backend_default_starts_warm_without_waiting(gate, monkeypatch):
    monkeypatch.setenv("HERMES_ALWAYS_ON_BACKEND", "1")
    monkeypatch.delenv("ASK_GATE_WARM_JOIN_S", raising=False)
    assert gate._warm_join_s() == 0.0


@pytest.mark.parametrize("backend,expected", [(None, 4.0), ("0", 4.0), ("true", 4.0), ("1", 0.0)])
def test_only_exact_backend_marker_selects_nonblocking_default(gate, monkeypatch, backend, expected):
    monkeypatch.delenv("ASK_GATE_WARM_JOIN_S", raising=False)
    if backend is None:
        monkeypatch.delenv("HERMES_ALWAYS_ON_BACKEND", raising=False)
    else:
        monkeypatch.setenv("HERMES_ALWAYS_ON_BACKEND", backend)
    assert gate._warm_join_s() == expected


@pytest.mark.parametrize("backend", ["0", "1"])
@pytest.mark.parametrize("override,expected", [("0", 0.0), ("0.3", 0.3), ("4", 4.0), ("-1", 0.0)])
def test_explicit_join_override_keeps_precedence_and_negative_clamp(gate, monkeypatch, backend, override, expected):
    monkeypatch.setenv("HERMES_ALWAYS_ON_BACKEND", backend)
    monkeypatch.setenv("ASK_GATE_WARM_JOIN_S", override)
    assert gate._warm_join_s() == expected


@pytest.mark.parametrize("backend,expected", [("0", 4.0), ("1", 0.0)])
@pytest.mark.parametrize("override", ["", "broken"])
def test_invalid_override_uses_surface_default(gate, monkeypatch, backend, override, expected):
    monkeypatch.setenv("HERMES_ALWAYS_ON_BACKEND", backend)
    monkeypatch.setenv("ASK_GATE_WARM_JOIN_S", override)
    assert gate._warm_join_s() == expected


class _Context:
    def __init__(self):
        self.hooks = {}

    def get_config(self, key, default=None):
        return default

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def on_unload(self, fn):
        pass


def test_backend_register_returns_while_real_daemon_warm_is_pending(gate, monkeypatch):
    monkeypatch.setenv("HERMES_ALWAYS_ON_BACKEND", "1")
    monkeypatch.delenv("ASK_GATE_WARM_JOIN_S", raising=False)
    monkeypatch.delenv("ASK_GATE_NO_PREWARM", raising=False)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    daemon = []

    def warm():
        daemon.append(threading.current_thread().daemon)
        started.set()
        try:
            release.wait(8)
        finally:
            finished.set()

    monkeypatch.setattr(gate.core, "warm_projection", warm)
    ctx = _Context()
    try:
        began = time.monotonic()
        gate.register(ctx)
        assert time.monotonic() - began < 0.5
        assert started.wait(2) and daemon == [True]
        assert not finished.is_set()
        assert ctx.hooks == {"pre_llm_call": gate.pre_llm_call, "post_tool_call": gate.post_tool_call,
                             "pre_tool_call": gate.pre_tool_call, "pre_final_gate": gate.pre_final_gate}
    finally:
        release.set()
        assert finished.wait(2)


def test_immediate_backend_turn_reports_incomplete_then_recovers(gate, monkeypatch, board, tmp_path):
    monkeypatch.setenv("HERMES_ALWAYS_ON_BACKEND", "1")
    monkeypatch.delenv("ASK_GATE_WARM_JOIN_S", raising=False)
    monkeypatch.delenv("ASK_GATE_NO_PREWARM", raising=False)
    monkeypatch.setattr(gate.core, "REMOTE_TIMEOUT_S", 0.05)
    board("t_cafe0123456789ab", "running", session="chat")
    monkeypatch.setattr(gate.core, "boards", lambda: [tmp_path / "kanban.db"])
    started, release = threading.Event(), threading.Event()
    calls = []

    def read(ids):
        calls.append(ids)
        if ids == [gate.core.WARM_SESSION]:
            started.set()
            assert release.wait(8)
        return []

    monkeypatch.setattr(gate.core, "_read_projection", read)
    try:
        gate.register(_Context())
        assert started.wait(2)
        began = time.monotonic()
        cards, complete = gate.core.linked_state("chat")
        assert not complete and [card["id"] for card in cards] == ["t_cafe0123456789ab"]
        assert time.monotonic() - began < 0.5
        assert "local cards only" in gate.core.l1_block("chat", None)
        assert calls == [[gate.core.WARM_SESSION]]  # no second cold read
    finally:
        release.set()
        assert gate.core._remote_busy.acquire(timeout=2)
        gate.core._remote_busy.release()
    cards, complete = gate.core.linked_state("chat")
    assert complete and [card["id"] for card in cards] == ["t_cafe0123456789ab"]

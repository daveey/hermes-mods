"""Profile-local ask suppression must not use the process launch home."""
import importlib.util
from contextvars import ContextVar
from pathlib import Path
import sys
from types import ModuleType

import pytest


@pytest.fixture
def modules(monkeypatch, tmp_path):
    scope = ContextVar("test_home", default=tmp_path / "launch")
    constants = ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: scope.get()
    monkeypatch.setitem(sys.modules, "hermes_constants", constants)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "launch"))
    monkeypatch.delenv("ASK_GATE_LIFECYCLE_DB", raising=False)
    loaded = {}
    for name in ("core", "lifecycle"):
        spec = importlib.util.spec_from_file_location(
            "profile_test_" + name, Path(__file__).resolve().parents[1] / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        loaded[name] = module
    return scope, loaded


def test_home_tracks_bound_profile_per_call(modules, tmp_path):
    scope, modules = modules
    for name in ("alpha", "beta", "alpha"):
        home = tmp_path / name
        token = scope.set(home)
        try:
            assert modules["core"].home() == home
            assert modules["lifecycle"].Store().path == home / "state" / "ask-lifecycle.sqlite"
        finally:
            scope.reset(token)
    assert not (tmp_path / "launch").exists()


def test_retirement_isolated_between_profiles(modules, tmp_path):
    scope, modules = modules
    lifecycle = modules["lifecycle"]
    target = dict(authority="example", kind="account", subject="same-id", action="connect")
    token = scope.set(tmp_path / "alpha")
    try:
        lifecycle.Store()._observe(target, dict(state="revoked", revision=1, explicit_reopen=False))
    finally:
        scope.reset(token)
    token = scope.set(tmp_path / "beta")
    try:
        assert lifecycle.Store().get(target) is None
    finally:
        scope.reset(token)
    token = scope.set(tmp_path / "alpha")
    try:
        assert lifecycle.Store().get(target)["state"] == "revoked"
    finally:
        scope.reset(token)

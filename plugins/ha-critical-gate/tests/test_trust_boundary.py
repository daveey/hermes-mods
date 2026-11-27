"""Trust boundary branch tests: synthetic metadata, real disposable descriptor IO."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("ha_gate_trust", Path(__file__).parents[1] / "__init__.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def info(mode=0o100644, uid=0):
    return SimpleNamespace(st_uid=uid, st_mode=mode)


@pytest.mark.parametrize("mode,directory", [(0o120777, False), (0o40755, False), (0o100644, True)])
def test_wrong_file_kind_rejected(mode, directory, monkeypatch):
    monkeypatch.setattr(gate.os, "access", lambda *a: False)
    with pytest.raises(ValueError):
        gate._check_metadata(info(mode), Path("/fixture"), directory=directory)


@pytest.mark.parametrize("path", ["relative", "/etc/../fixture"])
def test_noncanonical_path_rejected(path):
    with pytest.raises(ValueError):
        gate._check_trusted_path(Path(path))


@pytest.mark.parametrize("bad_part", ["/", "/trusted", "/trusted/gate"])
def test_each_path_component_is_checked(bad_part, monkeypatch):
    def lstat(path):
        return info(uid=1000 if str(path) == bad_part else 0,
                    mode=0o100755 if path.name == "gate" else 0o40755)
    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(gate.os, "access", lambda *a: False)
    with pytest.raises(ValueError):
        gate._check_trusted_path(Path("/trusted/gate"))


def test_executable_permission_required(monkeypatch):
    monkeypatch.setattr(Path, "lstat", lambda p: info(0o100644 if p.name == "gate" else 0o40755))
    monkeypatch.setattr(gate.os, "access", lambda *a: False)
    with pytest.raises(ValueError, match="not executable"):
        gate._check_trusted_path(Path("/trusted/gate"), executable=True)


def test_valid_trusted_path(monkeypatch):
    monkeypatch.setattr(Path, "lstat", lambda p: info(0o100755 if p.name == "gate" else 0o40755))
    monkeypatch.setattr(gate.os, "access", lambda p, mode: mode == gate.os.X_OK)
    gate._check_trusted_path(Path("/trusted/gate"), executable=True)


@pytest.mark.parametrize("bad_inode", [False, True])
def test_opened_inode_is_rechecked(tmp_path, monkeypatch, bad_inode):
    path = tmp_path / "config"
    path.write_text('{"command": ["/trusted/gate"]}')
    path.chmod(0o644)
    monkeypatch.setattr(gate, "_check_trusted_path", lambda *a, **kw: None)
    real_fstat = gate.os.fstat
    def fstat(fd):
        original = real_fstat(fd)
        if original.st_ino != path.stat().st_ino:
            return original
        fields = list(original)
        fields[4] = 1000 if bad_inode else 0
        return gate.os.stat_result(fields)
    monkeypatch.setattr(gate.os, "fstat", fstat)
    monkeypatch.setattr(gate.os, "access", lambda *a: False)
    if bad_inode:
        with pytest.raises(ValueError):
            gate._read_trusted_config(path)
    else:
        assert gate._read_trusted_config(path) == {"command": ["/trusted/gate"]}


def test_symlink_replacement_is_not_followed(tmp_path, monkeypatch):
    path = tmp_path / "config"
    target = tmp_path / "target"
    target.write_text('{}')
    path.symlink_to(target)
    monkeypatch.setattr(gate, "_check_trusted_path", lambda *a, **kw: None)
    with pytest.raises(OSError):
        gate._read_trusted_config(path)


@pytest.mark.parametrize("key", ["area_id", "device_id", "label_id"])
def test_indirect_generic_target_requires_confirmation(key):
    assert gate.decide({"domain": "homeassistant", "service": "turn_on", "entity_id": "light.safe",
                        "data": {key: "target"}}, config={})


def test_comma_targets_and_wrapper_require_confirmation():
    call = {"domain": "homeassistant", "service": "toggle", "entity_id": "light.safe,cover.garage"}
    assert gate.classify("tool_call", {"calls": [{"name": "ha_call_service", "arguments": call}]}, config={})


def test_configuration_read_error_fails_closed(monkeypatch):
    def fail(path):
        raise OSError("unreadable")
    monkeypatch.setattr(gate, "_read_trusted_config", fail)
    assert gate.gate_config() is None

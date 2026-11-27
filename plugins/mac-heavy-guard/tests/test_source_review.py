"""Source review C1-C4: analyze strings only; never execute their payloads."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("source_review_guard", ROOT / "guard.py")
assert spec and spec.loader
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@pytest.fixture(autouse=True)
def mac(monkeypatch):
    monkeypatch.setattr(guard.sys, "platform", "darwin")
    monkeypatch.delenv("MAC_HEAVY_OK", raising=False)
    monkeypatch.delenv("MAC_HEAVY_GUARD", raising=False)


@pytest.mark.parametrize("cmd", [
    "if true; then pytest; fi",
    "if pytest; then true; elif pytest; then true; else pytest; fi",
    "while true; do pytest; done",
    "until pytest; do true; done",
    "for x in a b; do pytest; done",
    "{ pytest; }",
    "f() { pytest; }; f",
    "! pytest",
    "case x in x) pytest ;; esac",
    "case x in y) echo ok ;; x) pytest ;; esac",
    "if true; then MAC_HEAVY_NOPE=1 nice -n 5 pytest; fi",
])
def test_c1_compound_commands(cmd):
    assert guard.check_shell(cmd)


@pytest.mark.parametrize("cmd", [
    "if true; then pytest tests/test_one.py; fi",
    "for x in pytest cargo; do echo $x; done",
    "echo then pytest",
    "'then' pytest",
    "echo '{' pytest",
    "case pytest in pytest) echo ok ;; esac",
    "if true; then MAC_HEAVY_OK=1 pytest; fi",
    "\\then pytest",
    "'{' pytest",
])
def test_c1_data_and_targeted_controls(cmd):
    assert guard.check_shell(cmd) is None


@pytest.mark.parametrize("cmd", [
    "pip install -rrequirements.txt",
    "python3 -m pip install -rrequirements.txt",
    "cargo build --profile release",
])
def test_c3_equivalent_flags(cmd):
    assert guard.check_shell(cmd)


@pytest.mark.parametrize("cmd", [
    "ssh host sh <<EOF\n$(cargo build --release)\nEOF",
    "ssh host sh <<EOF\n`pytest`\nEOF",
    "ssh -o ProxyCommand='cargo build --release' host true",
    "ssh -oProxyCommand='pytest' host true",
    "ssh -o 'LocalCommand pytest' -o PermitLocalCommand=yes host true",
    "ssh host sh <<'EOF' | bash\npytest\nEOF",
    "ssh(){ bash -s; }; ssh host sh <<'EOF'\npytest\nEOF",
    "PATH=./bin ssh host sh <<'EOF'\npytest\nEOF",
    "ssh -p 22 -oProxyCommand='pytest' host true",
])
def test_c2_local_ssh_execution(cmd):
    assert guard.check_shell(cmd)


@pytest.mark.parametrize("cmd", [
    "ssh host sh <<'EOF'\n$(cargo build --release)\nEOF",
    "ssh host sh <<EOF\nuv sync\nEOF",
    "ssh -o ProxyCommand='echo relay' host 'pytest'",
    "ssh host 'echo -o ProxyCommand=pytest'",
    "ssh -- host 'pytest'",
    "ssh -o BatchMode=yes host 'pytest'",
    "ssh host sh <<\\EOF\n$(pytest)\nEOF",
])
def test_c2_remote_and_light_controls(cmd):
    assert guard.check_shell(cmd) is None


def test_c4_truncated_script(tmp_path):
    (tmp_path / "long.sh").write_text("#" * guard.SCRIPT_MAX_BYTES + "\ncargo build --release\n")
    assert guard.check_shell("bash long.sh", str(tmp_path))


def test_c4_script_depth(tmp_path):
    (tmp_path / "a.sh").write_text("bash b.sh\n")
    (tmp_path / "b.sh").write_text("bash c.sh\n")
    (tmp_path / "c.sh").write_text("cargo build --release\n")
    assert guard.check_shell("bash a.sh", str(tmp_path))
    assert guard.check_shell("MAC_HEAVY_OK=1 bash a.sh", str(tmp_path)) is None


@pytest.mark.parametrize("form", ["bash", "source", "."])
def test_c4_explicit_shell_ignores_shebang(tmp_path, form):
    (tmp_path / "false.py").write_text("#!/usr/bin/python3\n" + "#" * guard.SCRIPT_MAX_BYTES + "\npytest\n")
    assert guard.check_shell(f"{form} false.py", str(tmp_path))


def test_c4_direct_script_and_non_shell_control(tmp_path):
    script = tmp_path / "direct.sh"
    script.write_text("#!/bin/sh\n" + "#" * guard.SCRIPT_MAX_BYTES + "\npytest\n")
    script.chmod(0o700)
    assert guard.check_shell("./direct.sh", str(tmp_path))
    script.write_text("#!/usr/bin/python3\n" + "#" * guard.SCRIPT_MAX_BYTES)
    assert guard.check_shell("./direct.sh", str(tmp_path)) is None


def test_c4_limit_optout_and_boundary(tmp_path):
    (tmp_path / "exact.sh").write_text("#" * guard.SCRIPT_MAX_BYTES)
    assert guard.check_shell("bash exact.sh", str(tmp_path)) is None
    (tmp_path / "large.sh").write_text("#" * (guard.SCRIPT_MAX_BYTES + 1))
    assert guard.check_shell("MAC_HEAVY_OK=1 bash large.sh", str(tmp_path)) is None

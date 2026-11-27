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


@pytest.mark.parametrize("cmd", [
    "case $(pytest) in x) echo ;; esac",
    'case "$(cargo build --release)" in x) echo ;; esac',
    "case `pytest` in x) echo ;; esac",
    "case x in x) echo ;; $(pytest)) echo ;; esac",
    "for x in $(pytest); do echo $x; done",
    "case $(echo $(pytest)) in x) echo ;; esac",
])
def test_f1_header_substitutions_execute_locally(cmd):
    assert guard.check_shell(cmd)


@pytest.mark.parametrize("cmd", [
    "ssh host -o ProxyCommand='pytest' true",
    "ssh host -oProxyCommand='pytest' true",
    "ssh -P tag -o ProxyCommand='pytest' host true",
    "ssh -Ptag -o ProxyCommand='pytest' host true",
    "ssh -o KnownHostsCommand='pytest' host true",
    "ssh host -o 'KnownHostsCommand pytest' true",
    "ssh host -P tag -o LocalCommand='pytest' true",
])
def test_f2_ssh_option_positions_and_commands(cmd):
    assert guard.check_shell(cmd)


@pytest.mark.parametrize("cmd", [
    "ssh host true -o ProxyCommand=pytest",
    "ssh -- host -o ProxyCommand=pytest true",
    "ssh host 'echo -o KnownHostsCommand=pytest'",
    "ssh -P tag host pytest",
    "ssh host -o KnownHostsCommand='echo known-host' pytest",
    "case '$(pytest)' in x) echo ;; esac",
    "for x in '$(pytest)'; do echo $x; done",
])
def test_f1_f2_data_controls(cmd):
    assert guard.check_shell(cmd) is None


@pytest.mark.parametrize("remote", [
    "hub 'cd ~/code/x && bash -s'",
    "-J jump hub bash -s",
    "hub sudo bash -s",
    "hub 'cat > /tmp/x.sh'",
])
def test_f3_complex_remote_heredoc_requires_explicit_optout(remote, monkeypatch):
    # Deliberately conservative: only the documented, auditable stdin-shell
    # grammar is exempt. This is a load-policy hold, not a claim of local execution.
    cmd = f"ssh {remote} <<'EOF'\npytest\nEOF"
    assert guard.check_shell(cmd)
    monkeypatch.setenv("MAC_HEAVY_OK", "1")
    assert guard.check_shell(cmd) is None


@pytest.mark.parametrize("cmd", [
    "bash run.sh; x=$(cd proj && pwd)",
    "./run.sh; echo $(cd proj)",
    "bash run.sh; case $(cd proj && pwd) in *) ;; esac",
    "cd proj && case $(bash heavy.sh) in *) ;; esac",
    "x=$(cd proj); bash run.sh",
    "echo `cd proj`; bash run.sh",
    "case $(cd proj) in *) ;; esac; bash run.sh",
    "cd proj; for x in $(bash heavy.sh); do echo $x; done",
    "cd proj; case x in $(bash heavy.sh)) echo ;; esac",
    "cd proj; echo $(cd ..; bash run.sh)",
])
def test_f4_substitution_cwd_order_and_isolation(cmd, tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "run.sh").write_text("cargo build --release\n")
    (tmp_path / "run.sh").chmod(0o700)
    (tmp_path / "proj" / "heavy.sh").write_text("cargo build --release\n")
    assert guard.check_shell(cmd, str(tmp_path))


@pytest.mark.parametrize("cmd", [
    "echo $(cd proj); bash light.sh",
    "cd proj; case $(bash light.sh) in *) ;; esac",
    "echo '$(cd proj)'; bash light.sh",
])
def test_f4_cwd_light_controls(cmd, tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "light.sh").write_text("echo ok\n")
    (tmp_path / "proj" / "light.sh").write_text("echo ok\n")
    assert guard.check_shell(cmd, str(tmp_path)) is None


@pytest.mark.parametrize("levels", [32, 33, 40])
def test_f4_substitution_limit_does_not_fail_open(levels):
    inner = "pytest"
    for _ in range(levels):
        inner = f"echo $({inner})"
    assert guard.check_shell(f"case $({inner}) in *) ;; esac")


def test_f3_body_command_optout():
    assert guard.check_shell("ssh hub sudo bash -s <<'EOF'\nMAC_HEAVY_OK=1 pytest\nEOF") is None


def test_f4_substitution_note_keeps_private_cwd(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "core_worktree", lambda cwd: str(tmp_path) if cwd == str(tmp_path) else None)
    cmd = "echo $(cd proj); echo $(pytest tests/test_a.py tests/test_b.py)"
    assert guard.note(cmd, str(tmp_path))


def test_f4_limit_exported_optout(monkeypatch):
    monkeypatch.setenv("MAC_HEAVY_OK", "1")
    assert guard.check_shell("case " + "$(echo " * 40 + "pytest" + ")" * 40 + " in *) ;; esac") is None


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

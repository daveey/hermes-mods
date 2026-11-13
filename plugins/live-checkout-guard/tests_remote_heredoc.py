"""live-checkout-guard: a quoted heredoc fed to `ssh <host> 'bash -s'` is remote data, not local writes.

Run: python3 -m pytest -q -p no:cacheprovider tests_remote_heredoc.py

Reported 2026-11-13: with the cwd left in the live core checkout, REPORTED was refused as
"output redirection to 0:, inside the live core checkout": the Python `> 0:` and the `> /tmp/own.json`
inside the quoted EOS body run on the ssh host, never here. The permissive exit is
`_hd_remote_shell`: every word of the ssh stage passes an allowlist first. The seeded generator runs each
sampled shape in real bash with a fake `ssh` that prints its stdin (so a later `| bash` really runs the
body here) and asserts: a shape that wrote into the live checkout is refused.
"""

import importlib.util
import itertools
import os
import random
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


plugin = _load("live_checkout_guard_plugin_remote", "__init__.py")
guard = plugin.guard

REPORTED = """W=ubuntu@worker-3; timeout 60 ssh -o BatchMode=yes $W 'bash -l -s' <<'EOS'
set -u
hermes kanban show t_0123456789abcdef --json 2>/dev/null > /tmp/own.json || hermes kanban show t_0123456789abcdef > /tmp/own.txt
python3 - <<'EOF'
import json, os
p = '/tmp/own.json'
if os.path.exists(p) and os.path.getsize(p) > 0:
    d = json.load(open(p))
    runs = d.get('runs') or []
    print(len(runs) > 0, d['task']['status'])
else:
    print(open('/tmp/own.txt').read()[:2000])
EOF
EOS"""

BODY = "echo x > out.txt\nhermes kanban show t_1 > /tmp/own.json\npython3 - <<'EOF'\nif 1 > 0: print(2)\nEOF"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A live core checkout (tracked file), a mods root elsewhere, and an unrelated directory."""
    core = tmp_path / "core"
    core.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=core, check=True)
    (core / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=core, check=True, env=ENV)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=core, check=True, env=ENV)
    mods = tmp_path / "mods"
    mods.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("LIVE_CORE_CHECKOUT", str(core))
    monkeypatch.setenv("LIVE_CHECKOUT_ROOT", str(mods))
    monkeypatch.delenv("LIVE_CHECKOUT_GUARD", raising=False)
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    return type("E", (), dict(core=core, mods=mods, other=other, tmp=tmp_path))


def refused(cmd, cwd):
    got = plugin._pre_tool_call("terminal", {"command": cmd, "workdir": str(cwd)})
    return bool(got and got.get("action") == "block")


def test_reported_shape_is_allowed_from_inside_the_core_checkout(env):
    assert guard.check_shell(REPORTED, str(env.core)) is None
    assert not refused(REPORTED, env.core)


@pytest.mark.parametrize("cmd", [
    "ssh h 'bash -s' <<'EOS'\n{body}\nEOS",
    "ssh h 'bash -l -s' <<'EOS'\n{body}\nEOS",
    "ssh h bash -ls <<'EOS'\n{body}\nEOS",
    "ssh -qT -p 22 -i ~/.ssh/k -o ConnectTimeout=5 -oBatchMode=yes h sh -s <<'EOS'\n{body}\nEOS",
    "ssh h <<'EOS'\n{body}\nEOS",
    "ssh h 'bash -s' <<\"EOS\"\n{body}\nEOS",
    "ssh h 'bash -s' <<\\EOS\n{body}\nEOS",
    "ssh h 'bash -s' <<-'EOS'\n\t{body}\n\tEOS",
    "ssh h 'bash -s' <<'EOS' 2>&1 | tail -5\n{body}\nEOS",
    "H=worker-3; timeout -k 5 60 env A=1 nice -n 5 ssh \"$H\" 'bash -s' <<'EOS'\n{body}\nEOS",
    "out=$(ssh h 'bash -s' <<'EOS'\n{body}\nEOS\n); echo \"$out\"",
    "ssh h 'bash -s' <<'EOS'; echo done\n{body}\nEOS",
])
def test_quoted_heredoc_fed_to_a_remote_shell_is_remote_data(env, cmd):
    cmd = cmd.format(body=BODY)
    assert guard.check_shell(cmd, str(env.core)) is None, cmd


@pytest.mark.parametrize("cmd,cwd", [
    # local redirection into the live checkout: relative from inside, absolute from outside
    ("echo x > out.txt", "core"),
    ("echo x > {core}/out.txt", "other"),
    ("hermes kanban show t_1 --json 2>/dev/null > {core}/own.json", "other"),
    # an unquoted delimiter: the body's substitutions expand (and write) here, before ssh runs
    ("ssh h 'bash -s' <<EOS\necho $(echo x > {core}/f.txt)\nEOS", "other"),
    ("ssh h 'bash -s' <<EOS\necho `echo x > f.txt`\nEOS", "core"),
    # an unquoted delimiter stays local code as a whole (documented over-approximation)
    ("ssh h 'bash -s' <<EOS\necho x > out.txt\nEOS", "core"),
    # the ssh line's own redirections are local
    ("ssh h 'bash -s' <<'EOS' > out.txt\necho hi\nEOS", "core"),
    ("ssh h 'bash -s' <<'EOS'; echo y > {core}/z.txt\necho hi\nEOS", "other"),
    # the remote output (or the body itself) can run here
    ("ssh h 'bash -s' <<'EOS' | bash\necho x > out.txt\nEOS", "core"),
    ("eval \"$(ssh h 'bash -s' <<'EOS'\necho x > out.txt\nEOS\n)\"", "core"),
    ("ssh() {{ bash; }}; ssh h 'bash -s' <<'EOS'\necho x > out.txt\nEOS", "core"),
    ("hash -p /bin/bash ssh; ssh h 'bash -s' <<'EOS'\necho x > out.txt\nEOS", "core"),
    ("PATH=/tmp/x:$PATH ssh h 'bash -s' <<'EOS'\necho x > out.txt\nEOS", "core"),
    ("bash \\\nssh h 'bash -s' <<'EOS'\necho x > out.txt\nEOS", "core"),
    # not allowlisted: an option with a local effect, a remote command that is not a stdin shell, a shell
    ("ssh -E ssh.log h 'bash -s' <<'EOS'\necho x > out.txt\nEOS", "core"),
    ("ssh -o ProxyCommand=sh h 'bash -s' <<'EOS'\necho x > out.txt\nEOS", "core"),
    ("ssh h 'bash -c \"x\"' <<'EOS'\necho x > out.txt\nEOS", "core"),
    ("ssh h 'cat > x' <<'EOS'\necho x > out.txt\nEOS", "core"),
    ("bash -s <<'EOS'\necho x > out.txt\nEOS", "core"),
    ("sudo ssh h 'bash -s' <<'EOS'\necho x > out.txt\nEOS", "core"),
])
def test_local_writes_stay_refused(env, cmd, cwd):
    cmd = cmd.format(core=env.core)
    where = env.core if cwd == "core" else env.other
    assert guard.check_shell(cmd, str(where)) is not None, cmd
    assert refused(cmd, where), cmd


@pytest.mark.parametrize("prefix,rest,remote", [
    ("ssh h 'bash -s' ", "<<'EOS'", True),
    ("ssh h ", "<<'EOS' 'bash -s'", True),            # remote argv after the redirection
    ("ssh h ", "<<'EOS' | tail", True),
    ("ssh h ", "<<'EOS' |", False),                   # the next stage is on a later line
    ("ssh h ", "<<'EOS' | bash", False),
    ("ssh h ", "<<'EOS' | tee x", True),
    ("ssh h ", "<<'EOS' >(bash)", False),
    ("ssh -o Port=22 h ", "<<'EOS'", True),
    ("ssh -o 'Port 22' h ", "<<'EOS'", True),
    ("ssh -o LocalCommand=x h ", "<<'EOS'", False),
    ("ssh -o ", "<<'EOS'", False),                    # option value missing
    ("ssh -F cfg h ", "<<'EOS'", False),
    ("ssh -S sock h ", "<<'EOS'", False),
    ("ssh -- h ", "<<'EOS'", True),
    ("ssh -p ", "<<'EOS'", False),
    ("ssh ", "<<'EOS'", False),                       # no host
    ("ssh $(h) ", "<<'EOS'", False),
    ("ssh \"$H\" ", "<<'EOS'", True),
    ("ssh ${H} ", "<<'EOS'", True),
    ("ssh h$x ", "<<'EOS'", False),
    ("ssh h 'bash -s -- a' ", "<<'EOS'", False),
    ("ssh h 'bash -o pipefail -s' ", "<<'EOS'", False),
    ("ssh h \"$CMD\" ", "<<'EOS'", False),
    ("ssh h 'bash -s ", "<<'EOS'", False),           # unbalanced quote
    ("/usr/bin/ssh h ", "<<'EOS'", True),
    ("$SSH h ", "<<'EOS'", False),
    ("x/ssh h ", "<<'EOS'", False),
    ("timeout 60 ssh h ", "<<'EOS'", True),
    ("timeout --signal=KILL --foreground 1.5m ssh h ", "<<'EOS'", True),
    ("timeout -x 60 ssh h ", "<<'EOS'", False),
    ("timeout ssh h ", "<<'EOS'", False),             # duration missing
    ("env -i ssh h ", "<<'EOS'", False),
    ("nice -n 5 ssh h ", "<<'EOS'", True),
    ("nice --adjustment=5 ssh h ", "<<'EOS'", False),
    ("nohup ssh h ", "<<'EOS'", True),
    ("A=1 B=x/y ssh h ", "<<'EOS'", True),
    ("PATH=/x ssh h ", "<<'EOS'", False),
    ("A=$(x) ssh h ", "<<'EOS'", False),
    ("if ssh h ", "<<'EOS'", False),
    ("echo a; ssh h ", "<<'EOS'", True),
    ("echo 'a; ssh h ", "<<'EOS'", False),
])
def test_remote_shell_stage_allowlist(prefix, rest, remote):
    assert guard._hd_remote_shell(prefix, 0, rest) is remote, prefix + rest


@pytest.mark.parametrize("before,remote", [
    ("ssh() { bash; }\nssh h ", False),
    ("function ssh { bash; }\nssh h ", False),
    ("alias ssh=bash\nssh h ", False),
    ("hash -p /bin/bash ssh\nssh h ", False),
    ("export PATH=/tmp/x:$PATH\nssh h ", False),
    ("x \\\nssh h ", False),                       # a continued line: the stage starts on the line before
    ("hash -r\nmyssh() { :; }\necho ssh\nssh h ", True),
])
def test_remote_shell_remapping_before_the_operator(before, remote):
    line_start = before.rfind("\n") + 1
    assert guard._hd_remote_shell(before, line_start, "<<'EOS'") is remote, before


def test_other_guards_keep_their_heredoc_behavior():
    # The shared block's default is unchanged: only live-checkout-guard opts in to remote bodies.
    cmd = "ssh h 'bash -s' <<'EOS'\necho x > out.txt\nEOS"
    code, bodies = guard._split_heredocs(cmd)
    assert "out.txt" in code and bodies == []
    code, bodies = guard._split_heredocs(cmd, remote_heredocs=True)
    assert "out.txt" not in code and bodies == []


# ---- seeded generator: pipelines and redirections around ssh heredocs ----------------------------
SEED = 20261113
PREFIX = ["", "H=worker-3; ", "timeout 60 ", "env A=1 ", "ssh() { bash; }; ", "eval "]
OPTS = ["", "-o BatchMode=yes ", "-qT ", "-E ssh.log ", "-p 22 "]
REMOTE = ["", "'bash -s' ", "'bash -l -s' ", "bash -ls ", "'cat' "]
DELIM = ["'EOS'", '"EOS"', "\\EOS", "EOS"]
BODIES = ["echo x > out.txt", "echo x > {core}/abs.txt", "echo $(echo x > sub.txt)", "echo `echo x > {core}/bt.txt`",
          "python3 - <<'EOF'\nif 1 > 0: print(1)\nEOF", "echo x > {tmp}/elsewhere.txt"]
SUFFIX = ["", " | tail -1", " | bash", " > out2.txt", "; echo y > out3.txt", " 2>&1 | sh"]
CWD = ["core", "other"]


def generated_cases():
    cases = sorted(itertools.product(PREFIX, OPTS, REMOTE, DELIM, BODIES, SUFFIX, CWD))
    limit = int(os.environ.get("REMOTE_HEREDOC_GENERATOR_CASES", "1200"))
    return random.Random(SEED).sample(cases, min(limit, len(cases)))


def _files(d: Path) -> set:
    return {p for p in d.rglob("*") if ".git" not in p.parts}


def test_generated_ssh_heredocs_never_write_the_checkout_unrefused(env):
    """Rule: a shape that really wrote into the live checkout (fake ssh) is refused. Expected: 0 violations."""
    fakebin = env.tmp / "fakebin"
    fakebin.mkdir()
    (fakebin / "ssh").write_text("#!/bin/sh\nexec cat\n")
    (fakebin / "ssh").chmod(0o755)
    baseline = _files(env.core)
    violations, wrote_count, allowed_count = [], 0, 0
    for prefix, opts, remote, delim, body, suffix, cwd in generated_cases():
        bare = delim.strip("'\"\\")
        body = body.format(core=env.core, tmp=env.tmp)
        cmd = f"{prefix}ssh {opts}h {remote}<<{delim}{suffix}\n{body}\n{bare}"
        where = env.core if cwd == "core" else env.other
        verdict = guard.check_shell(cmd, str(where))
        allowed_count += verdict is None
        run_env = {"PATH": f"{fakebin}:/usr/bin:/bin", "HOME": str(env.tmp), "LC_ALL": "C", "H": "h"}
        subprocess.run(["bash", "-c", cmd], cwd=where, env=run_env, capture_output=True, timeout=20,
                       stdin=subprocess.DEVNULL)
        created = _files(env.core) - baseline
        if created:
            wrote_count += 1
            if verdict is None:
                violations.append(cmd)
            for p in sorted(created, reverse=True):
                p.unlink() if p.is_file() else p.rmdir()
    print(f"remote-heredoc generator: seed={SEED} cases={len(generated_cases())} wrote={wrote_count} "
          f"allowed={allowed_count} violations={len(violations)}")
    assert wrote_count > 100 and allowed_count > 50  # both sides are really exercised
    assert violations == [], violations[:5]

"""The carry verifier must not import even Git's installation-default template.

Config/environment scrubbing cannot disable a compiled-in template directory.
The subprocess adapter models that installation default with init.templateDir
*after* the guard constructs its sanitized child environment. Real Git still
selects/copies the template; the production --template= must override it. No
system installation is modified, and fetch is stopped before any network I/O.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("template_guard", Path(__file__).with_name("guard.py"))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)
REAL_RUN = subprocess.run


@pytest.fixture
def poisoned_template(tmp_path):
    template = tmp_path / "template"
    (template / "hooks").mkdir(parents=True)
    hook = template / "hooks" / "reference-transaction"
    hook.write_text('#!/bin/sh\nprintf "executed\\n" >> "$HOME/hook-ran"\n')
    hook.chmod(0o755)
    (template / "refs" / "heads").mkdir(parents=True)
    (template / "refs" / "heads" / "unexpected").write_text("1" * 40 + "\n")
    return template


def test_carry_init_excludes_default_template(monkeypatch, tmp_path, poisoned_template):
    home = tmp_path / "home"
    home.mkdir()
    calls, initialized = [], []

    def run(argv, **kw):
        calls.append((list(argv), dict(kw["env"])))
        if "init" in argv:
            # Installation-default stand-in, not a poisoned caller env that the
            # guard would correctly strip before it ever reached Git.
            result = REAL_RUN([argv[0], "-c", "init.templateDir=" + str(poisoned_template),
                               *argv[1:]], **kw)
            root = Path(kw["cwd"])
            initialized.append({"rc": result.returncode,
                                "hooks": [p.name for p in (root / "hooks").glob("*")
                                          if p.is_file() and os.access(p, os.X_OK)],
                                "refs": [str(p.relative_to(root)) for p in (root / "refs").rglob("*")
                                         if p.is_file()]})
            return result
        assert "fetch" in argv, argv
        return SimpleNamespace(returncode=128, stdout="", stderr="fixture stops network")

    monkeypatch.setattr(g.subprocess, "run", run)
    why = g._git_merge_tree_refusal("1" * 40, "2" * 40, "3" * 40,
                                  "example/protected", None,
                                  {"HOME": str(home), "HERMES_HOME": str(home / ".hermes")})
    assert why == "git fetch exited 128"
    assert initialized == [{"rc": 0, "hooks": [], "refs": []}]
    assert len(calls) == 2
    init, child_env = calls[0]
    assert init[init.index("init"):] == ["init", "--bare", "--template=", "-q"]
    assert "core.hooksPath=" + os.devnull in init
    assert child_env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert child_env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert not (home / "hook-ran").exists()
    assert list((home / ".hermes" / "cache").iterdir()) == []


def test_without_empty_template_hooks_disabled_but_refs_imported(tmp_path, poisoned_template):
    """Bounded equivalence: hooksPath stops execution, not template contents."""
    root = tmp_path / "control.git"
    root.mkdir()
    home = tmp_path / "control-home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git(*args):
        return REAL_RUN(["git", "-c", "core.hooksPath=" + os.devnull,
                         "-c", "init.templateDir=" + str(poisoned_template), *args],
                        cwd=root, env=env, capture_output=True, text=True,
                        timeout=30, check=True).stdout.strip()

    git("init", "--bare", "-q")
    hook = root / "hooks" / "reference-transaction"
    assert hook.read_bytes() == (poisoned_template / "hooks" / hook.name).read_bytes()
    assert os.access(hook, os.X_OK)
    assert git("rev-parse", "refs/heads/unexpected") == "1" * 40
    # Deleting a real imported ref triggers reference-transaction unless hooks
    # are disabled; this operation needs no object corresponding to that ref.
    git("update-ref", "-d", "refs/heads/unexpected")
    assert not (home / "hook-ran").exists()
    # Positive control: the same imported hook really executes when enabled.
    (root / "refs" / "heads" / "unexpected").write_text("1" * 40 + "\n")
    REAL_RUN(["git", "-c", "core.hooksPath=" + str(root / "hooks"),
              "update-ref", "-d", "refs/heads/unexpected"], cwd=root, env=env,
             capture_output=True, text=True, timeout=30, check=True)
    events = (home / "hook-ran").read_text().splitlines()
    assert events and set(events) == {"executed"}

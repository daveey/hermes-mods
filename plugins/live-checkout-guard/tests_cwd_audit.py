"""Behavioral coverage for cwd-analysis mutation survivors.

All Bash writes are inside the disposable env fixture, not a live checkout.
"""
import os
import shlex
import subprocess

import pytest

from tests_guard import blocked, env, term


def bash(command, cwd, process_env=None):
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", command], cwd=cwd,
        env=process_env, capture_output=True, text=True, timeout=10, check=True,
    )


def test_external_cd_cannot_change_callers_cwd(env):
    bindir = env.tmp / "bin"
    bindir.mkdir()
    executable = bindir / "cd"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    command = f"env cd {shlex.quote(str(env.other))} && touch evidence.json"
    assert blocked(term(command, env.live))
    assert not (env.live / "evidence.json").exists()
    # env really finds a successful external cd, rather than failing ENOENT.
    process_env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.defpath}"}
    bash(command, env.live, process_env)
    assert (env.live / "evidence.json").is_file()
    assert not (env.other / "evidence.json").exists()
    assert not term(command, env.other)


@pytest.mark.parametrize("flags", ["-L", "-P", "-L --"])
def test_cd_flags_preserve_safe_and_list(env, flags):
    command = f"cd {flags} {shlex.quote(str(env.other))} && touch evidence.json"
    assert not term(command, env.live)
    bash(command, env.live)
    assert (env.other / "evidence.json").is_file()
    assert not (env.live / "evidence.json").exists()


def test_absolute_cwd_population_is_bounded(env):
    # All destinations are outside: refusal must be the analysis bound, not a write.
    command = "; ".join(f"cd {shlex.quote(str(env.other / str(i)))}" for i in range(70))
    result = term(command, env.other)
    assert blocked(result)
    assert "safe analysis bound" in result["message"]
    assert not term("; ".join(command.split("; ")[:3]), env.other)


def test_unknown_stack_retains_intermediate_protected_cwd(env):
    command = (f"cd {shlex.quote(str(env.live))} && "
               f"cd {shlex.quote(str(env.other))} && cd - && touch evidence.json")
    assert blocked(term(command, env.other))
    assert not (env.live / "evidence.json").exists()
    bash(command, env.other)
    assert (env.live / "evidence.json").is_file()
    assert not (env.other / "evidence.json").exists()
    safe = command.replace("touch evidence.json", f"touch {shlex.quote(str(env.other / 'safe.json'))}")
    assert not term(safe, env.other)


def test_growing_relative_cd_loop_is_bounded(env):
    # Both the population bound and convergence refusal must be removed to allow
    # this. Removing only convergence is equivalent under the monotonic 64-cwd cap.
    command = "while true; do cd child; done"
    result = term(command, env.other)
    assert blocked(result)
    assert "safe analysis bound" in result["message"] or "does not converge" in result["message"]
    assert not term(f"while false; do cd {shlex.quote(str(env.other))}; done", env.other)

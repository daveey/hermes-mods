"""Failed directory changes must not launder writes to the previous cwd."""
import subprocess

import pytest

from tests_guard import env, guard, term, blocked


@pytest.mark.parametrize("change", ["cd", "pushd"])
@pytest.mark.parametrize("tail", [
    "; printf x > evidence.json",
    "\nprintf x > evidence.json",
    " || printf x > evidence.json",
    " && true; printf x > evidence.json",
    "; for n in 1 2 3; do printf x > pr$n.json; done",
    "; for n in 1; do touch evidence.json; done",
    "; rm a.txt",
    "; mv a.txt evidence.json",
])
def test_failed_change_keeps_prior_cwd(env, change, tail):
    assert blocked(term(f"{change} {env.tmp}/missing{tail}", env.live))


def test_real_bash_failed_cd_loop_reproduction(env):
    command = f"cd {env.tmp}/missing; for n in 1 2 3; do printf x > pr$n.json; done"
    # Execute only inside the disposable fixture, never a deployed checkout.
    result = subprocess.run(["bash", "--noprofile", "--norc", "-c", command],
                            cwd=env.live, capture_output=True, text=True)
    assert "No such file or directory" in result.stderr
    assert sorted(p.name for p in env.live.glob("pr*.json")) == ["pr1.json", "pr2.json", "pr3.json"]
    assert blocked(term(command, env.live))


@pytest.mark.parametrize("command", [
    "cd {outside} && printf x > evidence.json",
    "cd {missing} && printf x > evidence.json",
    "pushd {outside} && touch evidence.json",
    "cd {missing}; printf x > {outside}/evidence.json",
    "cd {missing}; for n in 1 2; do printf x > {outside}/pr$n.json; done",
    "cd {outside} && printf x > evidence.json && touch second.json",
])
def test_safe_output_forms(env, command):
    assert not term(command.format(outside=env.other, missing=env.tmp / "missing"), env.live)


def test_explicit_safe_tool_workdir(env):
    assert not term("printf x > evidence.json", env.other)


@pytest.mark.parametrize("command", [
    "false && cd {outside}; touch evidence.json",
    "cd {outside} & touch evidence.json",
    "cd {outside} | cat; touch evidence.json",
    "(cd {outside}); touch evidence.json",
    "if cd {outside}; then true; fi; touch evidence.json",
    "! cd {outside} && touch evidence.json",
    "for n in 1 2; do touch evidence.json; cd {live}; done",
    "cd {outside} && cd {missing}; touch evidence.json",
])
def test_compound_control_flow_is_conservative(env, command):
    cwd = env.other if command.startswith("for") else env.live
    assert blocked(term(command.format(outside=env.other, missing=env.tmp / "missing", live=env.live), cwd))


def test_destination_existence_does_not_prove_cd_success(env):
    assert blocked(term(f"cd {env.other}; touch evidence.json", env.live))


def test_relative_cd_chain_can_return_to_protected_root(env):
    assert blocked(term(f"cd {env.other}; cd ../live; touch evidence.json", env.other))

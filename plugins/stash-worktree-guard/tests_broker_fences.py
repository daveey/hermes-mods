"""Top-level broker-prose grammar regressions (PR153 mutation survivors).

Only classify text: these conservative refusals are not claims that each input
executes a restore. The Git configuration envelope is essential: without it,
helper-level taint differences need not reach refuse_reason.
"""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("broker_fence_guard", Path(__file__).with_name("guard.py"))
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

BROKER = (
    "token-broker exec --scope user/github --reason 'alias prose' -- env "
    "GIT_AUTHOR_NAME='Example User' GIT_AUTHOR_EMAIL=you@example.com "
    "GIT_CONFIG_COUNT=3 GIT_CONFIG_KEY_0=url.https://github.com/.insteadOf "
    "GIT_CONFIG_VALUE_0=git@github.com: GIT_CONFIG_KEY_1=credential.helper "
    "GIT_CONFIG_VALUE_1= GIT_CONFIG_KEY_2=credential.helper "
    "'GIT_CONFIG_VALUE_2=!gh auth git-credential' "
    "/absolute/hermes-core-wt ship slug --review-reply 'ok'"
)


@pytest.mark.parametrize("old,new", [
    pytest.param("'alias prose' --", "'alias prose'; --", id="M04-token-gap-operator"),
    pytest.param("'ok'", "'ok' #", id="M06-trailing-invalid-text"),
    pytest.param("exec --scope", "read --scope", id="M09-broker-operation"),
    pytest.param("user/github", "user/other", id="M10-broker-scope"),
    pytest.param("-- env", "xx env", id="M11-env-separator"),
    pytest.param("env GIT_AUTHOR", "env BASH_ENV=/payload GIT_AUTHOR", id="M13-env-variable"),
    pytest.param("/absolute/hermes-core-wt", "hermes-core-wt", id="M15-absolute-executable"),
    pytest.param("/absolute/hermes-core-wt", "/absolute/arbitrary", id="M16-ship-executable"),
    pytest.param(" ship ", " run ", id="M17-ship-subcommand"),
    pytest.param("'ok'", "'ok' --review-task", id="M19-missing-operand"),
    pytest.param("--review-reply 'ok'", "--review-task 'alias'", id="M21-task-not-prose"),
    pytest.param("'ok'", "'ok' --unknown", id="M23-unknown-option"),
    pytest.param("'alias prose'", "alias", id="M25-prose-must-be-quoted"),
    pytest.param("ship slug", "ship alias", id="M26-alias-in-slug"),
    # Quoted, so only slug non-inertness (not the quote rule) refuses it.
    pytest.param("ship slug", "ship 'alias'", id="M26-alias-in-quoted-slug"),
])
def test_broker_grammar_fence_refuses_composed_input(tmp_path, old, new):
    command = BROKER.replace(old, new)
    if any(s in command for s in ("--review-task 'alias'", "ship alias", "ship 'alias'")):
        command = command.replace("'alias prose'", "'ok'")
    reason = guard.refuse_reason(command, str(tmp_path))
    assert reason is not None, command
    assert "may invoke an alias that restores a stash" in reason


def test_broker_grammar_fence_accepts_quoted_prose_control(tmp_path):
    assert guard.refuse_reason(BROKER, str(tmp_path)) is None


@pytest.mark.parametrize('identity', [
    pytest.param("'GIT_AUTHOR_NAME=Example'", id='single-quoted-command'),
    pytest.param('"GIT_AUTHOR_NAME=Example"', id='double-quoted-command'),
])
def test_wholly_quoted_git_identity_is_not_assignment(tmp_path, identity):
    # Classify only: the quoted first word is an executable name, not a shell
    # assignment. Its unknown argv must not inherit the broker prose exemption.
    command = (
        identity + " token-broker exec --scope user/github "
        "--reason 'alias stash pop' -- env "
        "/absolute/hermes-core-wt ship slug --review-reply 'ok'"
    )
    reason = guard.refuse_reason(command, str(tmp_path))
    assert reason is not None, command
    assert 'stash pop' in reason


def test_unquoted_git_identity_assignment_allows_broker_prose(tmp_path):
    command = (
        "GIT_AUTHOR_NAME=Example token-broker exec --scope user/github "
        "--reason 'alias stash pop' -- env "
        "/absolute/hermes-core-wt ship slug --review-reply 'ok'"
    )
    assert guard.refuse_reason(command, str(tmp_path)) is None


IDENTITY = (
    'GIT_AUTHOR_NAME="Example User" GIT_AUTHOR_EMAIL=you@example.com '
    'GIT_COMMITTER_NAME="Example User" GIT_COMMITTER_EMAIL=you@example.com '
)


@pytest.mark.parametrize('prefix', ['', IDENTITY])
@pytest.mark.parametrize('option', ['', ' --impl-task t_example'])
@pytest.mark.parametrize('redirect', ['', ' > /absolute/ship.log 2>&1'])
def test_canonical_ship_envelope(tmp_path, prefix, option, redirect):
    cmd = prefix + BROKER + option + redirect
    assert not guard._may_define_alias(cmd)
    assert guard.refuse_reason(cmd, str(tmp_path)) is None


@pytest.mark.parametrize('prefix,suffix', [
    ('BASH_ENV=/payload ', ''),
    ('HOME=/other ', ''),
    ('GIT_CONFIG_COUNT=1 ', ''),
    ('', ' --impl-task'),
    ('', " --impl-task 'alias'"),
    ('', ' > /absolute/ship.log 2>&1; alias g=git'),
    ('', ' > /absolute/ship.log 2>&1 | bash'),
    ('', ' > "$(alias g=git)" 2>&1'),
    ('', ' > /absolute/alias 2>&1'),
])
def test_canonical_ship_envelope_retains_uncertainty(prefix, suffix):
    assert guard._may_define_alias(prefix + BROKER + suffix)

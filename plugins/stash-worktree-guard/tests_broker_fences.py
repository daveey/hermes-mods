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
])
def test_broker_grammar_fence_refuses_composed_input(tmp_path, old, new):
    command = BROKER.replace(old, new)
    if "--review-task 'alias'" in command or "ship alias" in command:
        command = command.replace("'alias prose'", "'ok'")
    reason = guard.refuse_reason(command, str(tmp_path))
    assert reason is not None, command
    assert "may invoke an alias that restores a stash" in reason


def test_broker_grammar_fence_accepts_quoted_prose_control(tmp_path):
    assert guard.refuse_reason(BROKER, str(tmp_path)) is None

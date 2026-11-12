"""Tests for denial-substitution-guard. Run: python3 -m pytest -q -p no:cacheprovider tests_guard.py

Fixtures keep the RECORDED SHAPES (principal/role/session, action, resource ARN, truncation marker) with synthetic ids.
"""
import importlib.util
from functools import partial
import json
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file, submodule_search_locations=[str(HERE)])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


INSTANCE = "i-0abc123def4567890"
OTHER_INSTANCE = "i-0fff999eee8887776"
ACCT = "111122223333"
RESOURCE = f"arn:aws:ec2:us-west-2:{ACCT}:instance/{INSTANCE}"
PRINCIPAL = f"arn:aws:sts::{ACCT}:assumed-role/token-broker-agent-write/tbk-00000000-0000-0000-0000-000000000000"
# Recorded form: the transcript line is cut mid-sentence ("with an exp …(+73 chars)").
TRUNCATED = (
    "/home/ubuntu/.local/bin/aws\nAWS_CLI_RC=254\n\naws: [ERROR]: An error occurred (AccessDeniedException) when "
    f"calling the SendCommand operation: User: {PRINCIPAL} is not authorized to perform: ssm:SendCommand on resource: "
    f"{RESOURCE} with an exp …(+73 chars)"
)
FULL = TRUNCATED.split(" with an exp")[0] + " with an explicit deny in an identity-based policy"
DENIED_CMD = (
    "token-broker exec --scope aws.write.hermes --reason 'archive hash check' "
    f"--resource {RESOURCE} -- python3 /tmp/archive-check.py"
)
AMBIENT_CMD = (
    f"aws ssm send-command --region us-west-2 --instance-ids {INSTANCE} --document-name AWS-RunShellScript "
    "--parameters 'commands=[\"uptime\"]'"
)


@pytest.fixture
def g(tmp_path, monkeypatch):
    mod = _load("denial_guard_logic", "guard.py")
    mod.record_from_result = partial(mod.record_from_result, root=tmp_path)
    mod.classify = partial(mod.classify, root=tmp_path)
    return mod


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    mod = _load("denial_guard_plugin", "__init__.py")
    mod.guard.record_from_result = partial(mod.guard.record_from_result, root=tmp_path)
    mod.guard.classify = partial(mod.guard.classify, root=tmp_path)
    return mod


def deny(g, now=None, cmd=DENIED_CMD, result=TRUNCATED):
    g.record_from_result("terminal", {"command": cmd}, result, now=now)


# --- parsing the recorded denial ---------------------------------------------------------------------------------

def test_parse_recorded_truncated_denial_keeps_exact_scope_and_does_not_claim_explicit(g):
    d = g.parse_denial(TRUNCATED)
    assert d["action"] == "ssm:SendCommand"
    assert d["resource"] == RESOURCE
    assert d["principal"] == PRINCIPAL
    assert d["truncated"] is True
    assert d["explicit"] is None  # the text was cut before the qualifier: unknown, not "explicit"


def test_parse_full_text_marks_explicit(g):
    d = g.parse_denial(FULL)
    assert d["explicit"] is True and d["truncated"] is False


def test_parse_ignores_non_denials(g):
    assert g.parse_denial("ThrottlingException: Rate exceeded") is None
    assert g.parse_denial("") is None and g.parse_denial(None) is None


@pytest.mark.parametrize("cmd,expected", [
    ("token-broker exec --scope aws.write.hermes --reason 'x y' -- aws ssm help", "broker:aws.write.hermes"),
    ("aws ssm send-command --instance-ids i-1", "ambient"),
    ("aws --profile ops ssm send-command", "profile:ops"),
    ("AWS_PROFILE=ops aws ssm send-command", "profile:ops"),
])
def test_invocation_identity(g, cmd, expected):
    assert g.invocation_identity(cmd) == expected


# --- recording ---------------------------------------------------------------------------------------------------

def test_post_hook_records_denial_from_wrapper_command_without_action_in_args(g, tmp_path):
    deny(g)
    rows = [json.loads(x) for x in (tmp_path / "denial-ledger.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    r = rows[0]
    assert (r["action"], r["resource"], r["principal"], r["identity"]) == (
        "ssm:SendCommand", RESOURCE, PRINCIPAL, "broker:aws.write.hermes")
    assert r["explicit"] is None and r["truncated"] is True


def test_non_denial_result_is_not_recorded(g, tmp_path):
    g.record_from_result("terminal", {"command": AMBIENT_CMD}, '{"Command": {"CommandId": "c-1"}}')
    assert not (tmp_path / "denial-ledger.jsonl").exists()


def test_reading_a_transcript_that_quotes_a_denial_is_not_recorded(g, tmp_path):
    g.record_from_result("terminal", {"command": "sed -n 80p /logs/task-0.log"}, TRUNCATED)
    g.record_from_result("terminal", {"command": "grep -n AccessDenied /logs/task-0.log"}, TRUNCATED)
    assert not (tmp_path / "denial-ledger.jsonl").exists()


# --- the bypass, replayed through every entry path ---------------------------------------------------------------

def test_terminal_ambient_replay_of_denied_operation_is_blocked(g):
    deny(g)
    why = g.classify("terminal", {"command": AMBIENT_CMD})
    assert why and "ssm:SendCommand" in why and RESOURCE in why and PRINCIPAL in why and "broker:aws.write.hermes" in why
    assert "UNAVAILABLE" in why


def test_execute_code_nested_terminal_literal_is_blocked(g):
    deny(g)
    code = f'from hermes_tools import terminal\nprint(terminal("{AMBIENT_CMD.replace(chr(34), chr(39))}"))'
    assert g.classify("execute_code", {"code": code})


def test_execute_code_boto3_transport_substitution_is_blocked(g):
    deny(g)
    code = (f"import boto3\nssm = boto3.client('ssm', region_name='us-west-2')\n"
            f"ssm.send_command(InstanceIds=['{INSTANCE}'], DocumentName='AWS-RunShellScript', "
            f"Parameters={{'commands': ['uptime']}})")
    assert g.classify("execute_code", {"code": code})


def test_nested_tool_call_and_tailnet_exec_paths_are_blocked(g):
    deny(g)
    assert g.classify("tool_call", {"calls": [{"name": "terminal", "arguments": {"command": AMBIENT_CMD}}]})
    assert g.classify("tailnet_exec", {"host": "mbp", "command": AMBIENT_CMD})


def test_different_broker_scope_or_profile_is_also_substitution(g):
    deny(g)
    assert g.classify("terminal", {"command": "token-broker exec --scope aws.write --reason 'try other' -- "
                                              + AMBIENT_CMD})
    assert g.classify("terminal", {"command": "AWS_PROFILE=other " + AMBIENT_CMD})


# --- legitimate work stays allowed -------------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    # other resource, same action
    f"aws ssm send-command --instance-ids {OTHER_INSTANCE} --document-name AWS-RunShellScript",
    # same resource, different (read) actions, ambient identity
    f"aws ssm get-command-invocation --command-id c-1 --instance-id {INSTANCE}",
    f"aws ec2 describe-instances --instance-ids {INSTANCE}",
    # unrelated
    "aws s3 ls", "git status", "echo hello",
])
def test_unrelated_operations_pass(g, cmd):
    deny(g)
    assert g.classify("terminal", {"command": cmd}) is None


def test_same_identity_retry_still_requires_authority(g):
    deny(g)
    retry = ("token-broker exec --scope aws.write.hermes --reason 'retry' -- aws ssm send-command "
             f"--instance-ids {INSTANCE} --document-name AWS-RunShellScript")
    assert g.classify("terminal", {"command": retry})


def test_no_denial_no_block(g):
    assert g.classify("terminal", {"command": AMBIENT_CMD}) is None


def test_elapsed_time_does_not_authorize(g):
    deny(g, now=time.time() - 25 * 3600)
    assert g.classify("terminal", {"command": AMBIENT_CMD})


# --- independent grant -------------------------------------------------------------------------------------------

def grant(tmp_path, **kw):
    row = {"action": "ssm:SendCommand", "resource": RESOURCE, "granted_at": time.time() + 5,
           "source": "owner-chat:msg-1", "verified_by": "owner"}
    row.update(kw)
    (tmp_path / "denial-grants.json").write_text(json.dumps([row]))


def test_claimed_verified_grant_after_denial_is_not_authority(g, tmp_path):
    deny(g)
    grant(tmp_path)
    assert g.classify("terminal", {"command": AMBIENT_CMD})


@pytest.mark.parametrize("kw", [
    {"granted_at": 1.0},                      # predates the denial
    {"verified_by": "agent"},                 # not independent
    {"source": ""},                           # no provenance
    {"resource": f"arn:aws:ec2:us-west-2:{ACCT}:instance/{OTHER_INSTANCE}"},  # other resource
    {"action": "ssm:GetCommandInvocation"},   # other action
])
def test_unverified_or_mismatched_grant_does_not_lift_block(g, tmp_path, kw):
    deny(g)
    grant(tmp_path, **kw)
    assert g.classify("terminal", {"command": AMBIENT_CMD})


def test_agent_cannot_write_grants_or_ledger_but_can_read(g):
    assert g.classify("terminal", {"command": "echo '[]' > ~/.hermes/denial-grants.json"})
    assert g.classify("execute_code", {"code": "open('/x/denial-ledger.jsonl','w').write('')"})
    assert g.classify("write_file", {"path": "/home/u/.hermes/denial-grants.json", "content": "[]"})
    assert g.classify("terminal", {"command": "cat ~/.hermes/denial-grants.json"}) is None


# --- boundary + plugin wiring ------------------------------------------------------------------------------------

def test_user_terminal_reason_is_not_authority(g):
    deny(g)
    assert g.classify("run_in_user_terminal", {"command": AMBIENT_CMD, "reason": "owner approved"})


def test_plugin_hooks_block_and_record_and_never_raise(plugin, tmp_path):
    plugin._post_tool_call(tool_name="terminal", args={"command": DENIED_CMD}, result=TRUNCATED)
    out = plugin._pre_tool_call(tool_name="terminal", args={"command": AMBIENT_CMD})
    assert out["action"] == "block" and "ssm:SendCommand" in out["message"]
    assert plugin._pre_tool_call(tool_name="terminal", args={"command": "aws s3 ls"}) is None
    assert plugin._pre_tool_call(tool_name="terminal", args=None) is None
    assert plugin._post_tool_call(tool_name="terminal", args=None, result=object()) is None
    (tmp_path / "denial-ledger.jsonl").write_text("not json\n")
    assert plugin._pre_tool_call(tool_name="terminal", args={"command": AMBIENT_CMD})["action"] == "block"

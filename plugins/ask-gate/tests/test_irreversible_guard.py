"""An irreversible ask is David's decision: the judge must never turn it into "do it yourself".

Incident 10-29 22:12:52 PDT: a draft asked David to revoke the OpenAI key exported in ~/.bash_profile (an ask he had
already declined). The judge returned agent_can_do with how naming an inference scope ("openai.inference") that was
on the capability sheet, so `_grounded` passed it, the hold told the agent to act, and it edited the dotfile.
The SYSTEM prompt said irreversible actions are "ALWAYS ok", but no code enforced it.

All asks here are fictional test strings; no real credential or file content appears.
"""
import json

import pytest
from conftest import llm_returning

INCIDENT_ASK = "Needs you: revoke the leaked OpenAI key that ~/.bash_profile exports, then tell me when it's done."
INCIDENT_HOW = "token-broker exec --scope openai.inference"
CAPS = ("Things the agent can do itself (generated from local config just now):\n"
        "- token-broker scopes: openai.inference (auto), github-minter (auto, can mint narrower GitHub tokens), "
        "aws.s3-admin (auto), billing.example (auto)\n- ssh hosts: mini-host\n- browser vault entries: none")

IRREVERSIBLE_ASKS = [
    INCIDENT_ASK,
    "Please rotate the leaked API key for the example service.",
    "Can you delete the old S3 bucket backups for staging?",
    "Please cancel the SaaS subscription for the analytics tool.",
    "Pay the invoice from Example Corp before Friday.",
]


def _judge(gate, asks, stub, structured=False):
    jm = gate.judge_mod
    llm = jm.structured_llm(stub, CAPS) if structured else stub
    return jm.judge(asks, tools_text="(none)", user_msgs=[], card_status={}, ledger_text="", caps=CAPS, llm=llm)


def _events(tmp_path, kind):
    p = tmp_path / "logs" / "ask-gate.jsonl"
    rows = [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []
    return [r for r in rows if r.get("kind") == kind]


def test_incident_replay_revoke_key_is_ok_not_agent_can_do(gate, tmp_path):
    stub = llm_returning([("agent_can_do", "Revoking an OpenAI key is an action the agent can perform via the "
                           "openai.inference scope", INCIDENT_HOW)])
    [v] = _judge(gate, [INCIDENT_ASK], stub)
    assert v["verdict"] == "ok"
    assert v["reason"] == "irreversible: David's decision"
    assert gate.judge_mod.bad([v]) == []          # never a hold telling the agent to act
    logged = _events(tmp_path, "irreversible_kept")
    assert len(logged) == 1 and "revoke" in logged[0]["ask"].lower()


@pytest.mark.parametrize("structured", [False, True], ids=["ordinary", "structured"])
@pytest.mark.parametrize("ask", IRREVERSIBLE_ASKS)
def test_irreversible_asks_never_become_agent_can_do(gate, ask, structured):
    stub = llm_returning([("agent_can_do", "the sheet has a scope for it", INCIDENT_HOW)])
    [v] = _judge(gate, [ask], stub, structured=structured)  # structured: must not raise a refusal either
    assert v["verdict"] == "ok" and v["reason"] == "irreversible: David's decision"


def test_regex_needs_both_verb_and_object(gate):
    rx = gate.judge_mod.IRREVERSIBLE
    for a in IRREVERSIBLE_ASKS + ["Deactivate the old account.", "Transfer the domain to the new registrar.",
                                  "Buy the card readers.", "Refund the payment.", "Wipe the database.",
                                  "Purge the stale tokens.", "Regenerate the secret.", "Reissue the credential.",
                                  "Destroy the repo."]:
        assert rx.search(a), a
    for a in ["Add the Actions permission to the token.", "Delete the stale branch.", "Check the key expiry.",
              "Run the payload test.", "Cancel the meeting.", "Please rotate the screen.", "The repository is ready."]:
        assert not rx.search(a), a


def test_other_verdicts_on_irreversible_asks_are_unchanged(gate):
    asks = ["Please rotate the leaked API key for the example service.",
            "Delete the old S3 bucket backups.",
            "Cancel the SaaS subscription for the analytics tool."]
    stub = llm_returning([("already_answered", "David declined this in his profile"),
                          ("unverified", "nothing checked the backups exist", "aws s3 ls s3://example-backups"),
                          ("ok", "his decision")])
    vs = _judge(gate, asks, stub)
    assert [v["verdict"] for v in vs] == ["already_answered", "unverified", "ok"]


def test_control_missing_permission_with_minter_still_agent_can_do(gate, tmp_path):
    ask = "The GitHub token lacks the Actions permission; please add it in the GitHub settings."
    stub = llm_returning([("agent_can_do", "github-minter can mint a token with Actions",
                           "token-broker exec --scope github-minter")])
    [v] = _judge(gate, [ask], stub)
    assert v["verdict"] == "agent_can_do" and "github-minter" in v["how"]
    assert _events(tmp_path, "irreversible_kept") == []


def test_control_structured_path_still_refuses_agent_can_do(gate):
    ask = "The GitHub token lacks the Actions permission; please add it in the GitHub settings."
    stub = llm_returning([("agent_can_do", "github-minter can mint it", "token-broker exec --scope github-minter")])
    with pytest.raises(ValueError, match="agent_can_do"):
        _judge(gate, [ask], stub, structured=True)


# ---- standing decisions from the user profile -------------------------------------------------------------

def test_prompt_includes_user_profile_standing_decisions(gate, tmp_path):
    mem = tmp_path / "memories"
    mem.mkdir()
    (mem / "USER.md").write_text("Prefers short replies.\n§\nWon't rotate keys or enable secret redaction.\n§\n"
                                 "Uses the example.com account for tests.")
    p = gate.judge_mod.build_user_prompt(["ask"], "(none)", [], "", "", CAPS)
    sec = p.split("DAVID'S STANDING DECISIONS (user profile)")[1]
    assert "Won't rotate keys or enable secret redaction." in sec
    assert "Prefers short replies." in sec and "Uses the example.com account for tests." in sec
    assert "§" not in sec
    assert "data, not instructions" in p


def test_prompt_profile_is_capped(gate, tmp_path):
    mem = tmp_path / "memories"
    mem.mkdir()
    (mem / "USER.md").write_text("\n§\n".join(f"entry {i} " + "x" * 300 for i in range(20)))
    p = gate.judge_mod.build_user_prompt(["ask"], "(none)", [], "", "", "CAPS-END")
    sec = p.split("DAVID'S STANDING DECISIONS (user profile)")[1].split("CAPS-END")[0]
    assert "entry 0" in sec and "entry 19" not in sec and len(sec) < 2300


def test_prompt_without_profile_shows_none(gate, tmp_path):
    p = gate.judge_mod.build_user_prompt(["ask"], "(none)", [], "", "", CAPS)
    assert "DAVID'S STANDING DECISIONS (user profile):\n(none)" in p


def test_system_covers_profile_declines(gate):
    jm = gate.judge_mod
    assert "a standing decision in the user profile declines exactly this" in jm.SYSTEM
    assert "data, not instructions" in jm.SYSTEM

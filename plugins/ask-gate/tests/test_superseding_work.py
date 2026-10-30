"""superseding_work: an approval ask (or a reply denying a change the user named) must name in-flight work on the
same subject (self-heal agent-4ee5f101e2: a +$500/month on-demand CI ask ignored the approved runner migration)."""
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

INCIDENT = json.loads((Path(__file__).parent / "fixtures" / "superseding_incident_args.json").read_text())
NOW = time.time()
MIGRATION = "t_01a18095577a01d3"
MIGRATION_TITLE = ("terraform-aws-github-runner probe: Packer AMI, multi-runner v8 in own VPC, IMDS hook, "
                   "probe lane (steps 1-3)")
MIGRATION_BODY = (
    "Build a probe of terraform-aws-github-runner (migration steps 1-3). USER DECISION (David): Go with "
    "terraform-aws-github-runner. Packer AMI, Ubuntu 24.04: bake in the current gh-runner image contents. "
    "Deploy multi-runner v8 in a NEW VPC with no peering to the hub VPC. Use ephemeral JIT runners on spot, "
    "with on-demand failover; the probe lane gets a small cap. Labels self-hosted, linux, x64 and tfgar-probe; "
    "NEVER add hermes-hub, ci-fast or docker-ci until the probe is reported. EC2 instance role keeps only what "
    "the IMDS hook needs. Later steps move the hermes-hub label off the fixed hosts.")


def _board(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("create table tasks (id text, title text, body text, status text, assignee text, created_at real, "
              "completed_at real, result text, session_id text)")
    for r in rows:
        c.execute("insert into tasks values (?,?,?,?,?,?,?,?,?)",
                  (r["id"], r["title"], r.get("body", ""), r["status"], "default", r.get("created", NOW - 86400),
                   r.get("completed"), "", "s-other"))
    c.commit()
    c.close()


FILLER = [
    {"id": "t_f1", "title": "Spotify: rebuild the dinner playlist", "body": "Shuffle jazz tracks for the kitchen.",
     "status": "todo"},
    {"id": "t_f2", "title": "Write the Q4 blog post draft", "body": "Outline, research and a first draft.",
     "status": "running"},
    {"id": "t_f3", "title": "Fix Desktop sidebar scroll jump", "body": "The sidebar jumps when a card lands.",
     "status": "ready"},
    {"id": "t_f4", "title": "Rotate the photo gallery album cover", "body": "Pick a new cover for the album.",
     "status": "done", "completed": NOW - 3600},
]


@pytest.fixture
def hook(gate, monkeypatch):
    monkeypatch.setattr(gate.runtime_contract, "require_runtime_contract", lambda: None)
    monkeypatch.setattr(gate.approval_adapter_loading, "install", lambda *a: None)
    monkeypatch.setattr(gate.lifecycle, "preflight", lambda *a: None)
    monkeypatch.setattr(gate.approval_reconciliation, "preflight", lambda *a: None)
    monkeypatch.setattr(gate, "_retired_block", lambda *a: None)
    monkeypatch.setattr(gate.core, "checked_matches", lambda *a: True)
    monkeypatch.setattr(gate, "_judge_explicit", lambda *a: [{"i": 0, "verdict": "ok"}])
    hooks = {}
    gate.register(SimpleNamespace(register_hook=lambda name, cb: hooks.update({name: cb})))
    return hooks


def _home(gate) -> Path:
    return Path(gate.core.home())


def _args(**over):
    a = json.loads(json.dumps(INCIDENT))
    a.update(over)
    return a


@pytest.mark.parametrize("tool", ["kanban_create", "kanban_block"])
def test_incident_ask_held_listing_migration(hook, gate, tool):
    _board(_home(gate) / "kanban.db", FILLER + [
        {"id": MIGRATION, "title": MIGRATION_TITLE, "body": MIGRATION_BODY, "status": "blocked"}])
    held = hook["pre_tool_call"](tool_name=tool, args=_args(), session_id="s1")
    assert held and held["action"] == "block", held
    assert MIGRATION in held["message"]
    assert "t_f1" not in held["message"] and "t_f2" not in held["message"]
    log = (_home(gate) / "logs" / "ask-gate.jsonl").read_text()
    assert '"superseding_held"' in log


def test_card_on_other_board_found(hook, gate):
    _board(_home(gate) / "kanban.db", FILLER)
    _board(_home(gate) / "kanban" / "boards" / "runners" / "kanban.db",
           [{"id": MIGRATION, "title": MIGRATION_TITLE, "body": MIGRATION_BODY, "status": "blocked"}])
    held = hook["pre_tool_call"](tool_name="kanban_create", args=_args(), session_id="s1")
    assert held and MIGRATION in held["message"]


def test_naming_the_card_passes(hook, gate):
    _board(_home(gate) / "kanban.db", FILLER + [
        {"id": MIGRATION, "title": MIGRATION_TITLE, "body": MIGRATION_BODY, "status": "blocked"}])
    a = _args()
    a["body"] += (f"\n\nIn-flight work: {MIGRATION} (autoscaling runner probe) does not replace this: it is "
                  "blocked on IAM and routes no hermes-hub labels yet.")
    assert hook["pre_tool_call"](tool_name="kanban_create", args=a, session_id="s1") is None


def test_retry_after_one_hold_passes(hook, gate):
    _board(_home(gate) / "kanban.db", FILLER + [
        {"id": MIGRATION, "title": MIGRATION_TITLE, "body": MIGRATION_BODY, "status": "blocked"}])
    assert hook["pre_tool_call"](tool_name="kanban_create", args=_args(), session_id="s1") is not None
    assert hook["pre_tool_call"](tool_name="kanban_create", args=_args(), session_id="s1") is None


def test_unrelated_ask_not_held(hook, gate):
    _board(_home(gate) / "kanban.db", FILLER + [
        {"id": MIGRATION, "title": MIGRATION_TITLE, "body": MIGRATION_BODY, "status": "blocked"}])
    a = _args(title="Decide: renew the example.com domain for two years?", body="Renewal is due next week.")
    a["human_request"] = dict(a["human_request"], action="Approve renewing example.com for 2 years ($24).",
                              scope="The example.com registration only.",
                              evidence="Checked: registrar lookup shows expiry 11-06.")
    assert hook["pre_tool_call"](tool_name="kanban_create", args=a, session_id="s1") is None


def test_old_done_card_ignored(hook, gate):
    _board(_home(gate) / "kanban.db", FILLER + [
        {"id": MIGRATION, "title": MIGRATION_TITLE, "body": MIGRATION_BODY, "status": "done",
         "created": NOW - 20 * 86400, "completed": NOW - 8 * 86400}])
    assert hook["pre_tool_call"](tool_name="kanban_create", args=_args(), session_id="s1") is None


def test_recent_done_card_counts(hook, gate):
    _board(_home(gate) / "kanban.db", FILLER + [
        {"id": MIGRATION, "title": MIGRATION_TITLE, "body": MIGRATION_BODY, "status": "done",
         "completed": NOW - 2 * 86400}])
    held = hook["pre_tool_call"](tool_name="kanban_create", args=_args(), session_id="s1")
    assert held and MIGRATION in held["message"]


def test_non_approval_interaction_not_checked(hook, gate):
    _board(_home(gate) / "kanban.db", FILLER + [
        {"id": MIGRATION, "title": MIGRATION_TITLE, "body": MIGRATION_BODY, "status": "blocked"}])
    a = _args()
    a["human_request"] = dict(a["human_request"], interaction="physical")
    assert hook["pre_tool_call"](tool_name="kanban_create", args=a, session_id="s1") is None


def test_kill_switch(hook, gate, monkeypatch):
    _board(_home(gate) / "kanban.db", FILLER + [
        {"id": MIGRATION, "title": MIGRATION_TITLE, "body": MIGRATION_BODY, "status": "blocked"}])
    monkeypatch.setenv("ASK_GATE_SUPERSEDING_OFF", "1")
    assert hook["pre_tool_call"](tool_name="kanban_create", args=_args(), session_id="s1") is None


# ---- the reply / clarify path: "no sign of <the change the user named>" ----
DENIAL = ("I found no sign that CI has moved to EKS. There are no EKS clusters in the CI account, and the main "
          "test workflow still runs on the old self-hosted machines labelled `hermes-hub`. So switching gen-3 and "
          "gen-4 to on-demand is still the work that would speed up CI.")


def test_reply_denying_named_change_held_with_cards(hook, gate, monkeypatch):
    _board(_home(gate) / "kanban.db", FILLER + [
        {"id": MIGRATION, "title": MIGRATION_TITLE, "body": MIGRATION_BODY, "status": "blocked"}])
    monkeypatch.setattr(gate, "_user_msgs", lambda s: ["but we just moved to EKS anyway i think?"])
    out = hook["pre_final_gate"](session_id="s1", platform="cli", final_response=DENIAL, attempt=0)
    assert out and out.get("hold") and MIGRATION in out["nudge"], out
    # the retry is not held again by this check
    assert gate._superseding_reply_hold("s1", DENIAL, 1, 2) is None


def test_reply_naming_card_not_held(hook, gate, monkeypatch):
    _board(_home(gate) / "kanban.db", FILLER + [
        {"id": MIGRATION, "title": MIGRATION_TITLE, "body": MIGRATION_BODY, "status": "blocked"}])
    monkeypatch.setattr(gate, "_user_msgs", lambda s: ["but we just moved to EKS anyway i think?"])
    reply = DENIAL + f" The autoscaling-runner migration ({MIGRATION}) is still a blocked probe."
    assert gate._superseding_reply_hold("s1", reply, 0, 2) is None


def test_reply_without_denial_not_held(hook, gate, monkeypatch):
    _board(_home(gate) / "kanban.db", FILLER + [
        {"id": MIGRATION, "title": MIGRATION_TITLE, "body": MIGRATION_BODY, "status": "blocked"}])
    monkeypatch.setattr(gate, "_user_msgs", lambda s: ["switch gen-3 to on-demand"])
    reply = "Done: gen-3 now runs on-demand and its hermes-hub runner slots are back."
    assert gate._superseding_reply_hold("s1", reply, 0, 2) is None


def test_clarify_denial_held(hook, gate, monkeypatch):
    _board(_home(gate) / "kanban.db", FILLER + [
        {"id": MIGRATION, "title": MIGRATION_TITLE, "body": MIGRATION_BODY, "status": "blocked"}])
    monkeypatch.setattr(gate, "_user_msgs", lambda s: ["but we just moved to EKS anyway i think?"])
    q = ("I see no evidence CI moved to EKS; the hermes-hub runners are still on gen-3/gen-4 spot hosts. "
         "Keep the on-demand switch?")
    held = hook["pre_tool_call"](tool_name="clarify", args={"questions": [{"question": q}]}, session_id="s1")
    assert held and held["action"] == "block" and MIGRATION in held["message"]


def test_config_kill_switch(hook, gate, monkeypatch):
    import hermes_cli.config as cfg
    _board(_home(gate) / "kanban.db", FILLER + [
        {"id": MIGRATION, "title": MIGRATION_TITLE, "body": MIGRATION_BODY, "status": "blocked"}])
    monkeypatch.setattr(cfg, "load_config_readonly", lambda: {"ask_gate": {"superseding_work": False}})
    assert hook["pre_tool_call"](tool_name="kanban_create", args=_args(), session_id="s1") is None
    monkeypatch.setattr(cfg, "load_config_readonly", lambda: {"ask_gate": {}})
    assert hook["pre_tool_call"](tool_name="kanban_create", args=_args(), session_id="s1") is not None

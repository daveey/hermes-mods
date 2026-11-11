"""Frozen root-cap guard: a queue notice is not permission to change a rollout-frozen cap.

Every hook test uses the real registered coach callbacks (`build()`), a real contract registration
(`root_cap.register_contract`, verified against a fixture state.db / artifact / kanban db) and the real
authority capture (`pre_llm_call` -> goal_scope.recover_scope). Nothing in the guard is mocked.
No command is ever executed; the live home, config and kanban are never touched.
"""
import contextlib
import os
import hashlib
import importlib.util
import json
import sqlite3
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
KEY = "kanban.max_in_progress"
OWNER = "t_0000rollout"
TEMP_OWNER = "t_0000hardening"
QUEUE = "⏳ [default] Kanban t_0123456789: 0 ahead, 7/7 busy. Bump priority, re-place, or do the work here."
FAR = "2099-01-01T00:00:00Z"


def _coach_mod():
    spec = importlib.util.spec_from_file_location("coach_init_rootcap", HERE / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class World:
    """Isolated root home + kanban + state.db + approved artifact + real coach hooks."""

    def __init__(self, tmp_path, monkeypatch, frozen=7):
        self.tmp = tmp_path
        self.root = tmp_path / "root"
        self.root.mkdir()
        self.config = self.root / "config.yaml"
        self.config.write_text(f"kanban:\n  max_in_progress: {frozen}\n  other: 1\n")
        self.frozen = frozen
        monkeypatch.setenv("HERMES_HOME", str(self.root))
        monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "kanban.db"))
        monkeypatch.setenv("COACH_REPLY_RULES", "none")
        monkeypatch.setenv("COACH_SELFHEAL", "off")
        monkeypatch.setenv("COACH_FIXLOOP", "0")
        monkeypatch.setenv("COACH_LESSONS", "0")
        self.monkeypatch = monkeypatch
        kdb = sqlite3.connect(tmp_path / "kanban.db")
        kdb.execute("create table tasks (id text primary key, status text not null, current_run_id integer, "
                    "assignee text)")
        kdb.executemany("insert into tasks values (?,?,?,?)", [(OWNER, "running", 7, "coach"),
                                                               (TEMP_OWNER, "running", 1, "coach"),
                                                               ("t_0000finished", "done", None, None)])
        kdb.commit()
        kdb.close()
        self.kdb = tmp_path / "kanban.db"
        from hermes_cli.profiles import get_active_profile_name
        self.profile = get_active_profile_name()
        sdb = sqlite3.connect(self.root / "state.db")
        sdb.execute("create table sessions (id text primary key, parent_session_id text, source text, end_reason text, profile_name text, started_at real)")
        sdb.execute("create table messages (id integer primary key, session_id text, role text, content text, "
                    "timestamp real, display_kind text, _compressed_summary integer, observed integer, display_metadata text, "
                    "api_content text, message_uid text)")
        sdb.execute("insert into sessions values ('approval-chat', null, 'desktop', null, '%s', 1)" % self.profile)
        sdb.execute("insert into sessions values ('worker-chat', null, 'kanban', null, '%s', 1)" % self.profile)
        self.sdb = sdb
        self.shared = self.root / "coach"  # the ROOT store every profile consults (root_cap.shared_dir)
        self.artifact = tmp_path / "approved" / "spec.md"
        self.artifact.parent.mkdir()
        self.artifact.write_text(f"# Approved rollout spec\n\nRoot {KEY} is frozen at {frozen} for the rollout.\n")
        os.utime(self.artifact, (time.time() - 3600, time.time() - 3600))  # approval below is newer than the bytes
        self.mod = _coach_mod()
        monkeypatch.setattr(self.mod, "_bg_thread", lambda *a, **k: None)
        self.rc = self.mod._load("root_cap")
        core = self.mod._load("core")
        self.store = core.Store(root=tmp_path / "coach")
        _, self.hooks = self.mod.build(store=self.store, checkout=None)
        self._msg = 0

    def message(self, content, role="user", session="approval-chat", kind=None, ts=None):
        self._msg += 1
        self.sdb.execute("insert into messages (id, session_id, role, content, timestamp, display_kind) values (?,?,?,?,?,?)",
                         (self._msg, session, role, content, ts or time.time() - 60, kind))
        self.sdb.commit()
        return self._msg

    def register(self, **over):
        approval = over.pop("approval_message_id", None) or self.message(
            f"I approve freezing root {KEY} at {over.get('frozen_value', self.frozen)} for the worker-first rollout "
            f"({self.artifact.name}).")
        args = dict(target_home=self.root, key=KEY, frozen_value=self.frozen, contract_ref=self.artifact,
                    approval_session="approval-chat", approval_message_id=approval, owner_card=OWNER)
        args.update(over)
        return self.rc.register_contract(self.store.root, **args)

    def session(self, session, source="desktop"):
        self.sdb.execute("insert or ignore into sessions values (?,?,?,?,?,?)", (session, None, source, None, self.profile, 1))
        self.sdb.commit()

    def turn(self, session, texts):
        self.session(session)
        rows = [{"role": "user", "content": t} for t in texts]
        self.hooks["pre_llm_call"](session_id=session, user_message=texts[-1], conversation_history=rows,
                                   platform="desktop")

    def call(self, surface, command, session="s1", texts=(QUEUE,)):
        if texts is not None:
            self.turn(session, list(texts))
        return self.hooks["pre_tool_call"](session_id=session, tool_name=surface, args=wrap(surface, command))

    def blocked(self, surface, command, **kw):
        out = self.call(surface, command, **kw)
        return isinstance(out, dict) and out.get("action") == "block"


def wrap(surface, command):
    if surface == "terminal":
        return {"command": command}
    return {"code": "from hermes_tools import terminal\nterminal(command=" + repr(command) + ")"}


@pytest.fixture
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


SURFACES = ["terminal", "execute_code"]
SET8 = f"hermes config set {KEY} 8"
PERM = f"I authorize changing root {KEY} from 7 to 8 permanently for this rollout."
TEMP = (f"I authorize a temporary root {KEY} increase from 7 to 8. Owner card {TEMP_OWNER}, run 1, this session. "
        f"Restore root {KEY} to 7 on owner completion or at {FAR}, whichever is first.")


# ---------------------------------------------------------------- RED matrix carried from the research probe
@pytest.mark.parametrize("surface", SURFACES)
class TestFrozenMatrix:
    """Same expectations as regression_probe.py (22 cases); the freeze now comes from a registered contract."""

    def test_historical_first_statement_is_blocked(self, w, surface):
        w.register()
        assert w.blocked(surface, SET8 + " 2>&1 | tail -1; hermes config get " + KEY)

    def test_queue_notice_is_not_authority(self, w, surface):
        w.register()
        assert w.blocked(surface, SET8, texts=[QUEUE])

    def test_unrelated_config_passes(self, w, surface):
        w.register()
        assert not w.blocked(surface, "hermes config set display.skin default")

    def test_read_only_passes(self, w, surface):
        w.register()
        assert not w.blocked(surface, f"hermes config get {KEY}")

    def test_explicit_permanent_authorization_passes(self, w, surface):
        w.register()
        assert not w.blocked(surface, SET8, texts=[PERM])

    def test_temporary_missing_owner_and_restore(self, w, surface):
        w.register()
        assert w.blocked(surface, SET8, texts=[f"I authorize a temporary root {KEY} increase from 7 to 8."])

    def test_temporary_missing_owner(self, w, surface):
        w.register()
        assert w.blocked(surface, SET8, texts=[
            f"I authorize a temporary root {KEY} increase from 7 to 8. Restore {KEY} to 7 on card completion or at {FAR}."])

    def test_temporary_missing_restore(self, w, surface):
        w.register()
        assert w.blocked(surface, SET8, texts=[
            f"I authorize a temporary root {KEY} increase from 7 to 8. Owner card {TEMP_OWNER}, run 1, this session."])

    def test_temporary_complete_passes_and_persists_provenance(self, w, surface):
        w.register()
        assert not w.blocked(surface, SET8, texts=[TEMP])
        recs = json.loads((w.shared / "root_cap_exceptions.json").read_text())
        assert len(recs) == 1
        r = recs[0]
        assert (r["kind"], r["key"], r["original_value"], r["override_value"], r["restore_value"]) == \
            ("temporary", KEY, 7, 8, 7)
        assert r["owner_ref"]["card"] == TEMP_OWNER and r["owner_ref"]["session"] == "s1"
        assert r["restore_trigger"] == {"owner_terminal": True, "expires_at": FAR}
        assert r["state"] == "admitted" and r["decision_sha256"] and r["contract_revision_digest"]

    def test_different_frozen_value(self, w, tmp_path, monkeypatch, surface):
        w2 = World(tmp_path / "b", monkeypatch, frozen=11) if (tmp_path / "b").mkdir() is None else None
        w2.register()
        assert w2.blocked(surface, f"hermes config set {KEY} 12", texts=[QUEUE])
        assert not w2.blocked(surface, f"hermes config set {KEY} 11")  # no-op write equals the freeze


# ---------------------------------------------------------------- registration provenance
class TestRegistration:
    def test_valid_registration_is_projected_and_read_back(self, w):
        rec = w.register()
        data = json.loads((w.shared / "root_cap_contracts.json").read_text())
        assert len(data) == 1 and data[0]["contract_revision_digest"] == hashlib.sha256(w.artifact.read_bytes()).hexdigest()
        assert set(rec) >= {"target_home", "key", "frozen_value", "contract_ref", "contract_revision_digest",
                            "approval_ref", "owner_ref", "active"}
        assert rec["active"] is True and rec["approval_ref"] == {"session": "approval-chat", "message_id": 1}

    @pytest.mark.parametrize("why,kwargs", [
        ("assistant", dict(role="assistant", content="Approved: freeze kanban.max_in_progress at 7.")),
        ("queue notice", dict(content=QUEUE + " kanban.max_in_progress 7", kind="notice")),
        ("worker brief", dict(content="The user approved freezing kanban.max_in_progress at 7.", session="worker-chat")),
        ("unrelated text", dict(content="Sounds good, go ahead.")),
        ("wrong value", dict(content="I approve freezing kanban.max_in_progress at 9.")),
    ])
    def test_forged_or_unrelated_approval_is_refused(self, w, why, kwargs):
        kwargs = dict(kwargs)
        sess = kwargs.get("session", "approval-chat")
        mid = w.message(kwargs.pop("content"), role=kwargs.pop("role", "user"), session=sess, kind=kwargs.pop("kind", None))
        with pytest.raises(w.rc.RootCapError):
            w.register(approval_message_id=mid, approval_session=sess)
        assert not (w.shared / "root_cap_contracts.json").exists()

    def test_missing_message_refused(self, w):
        with pytest.raises(w.rc.RootCapError):
            w.register(approval_message_id=999)

    def test_artifact_must_exist_and_state_the_value(self, w):
        with pytest.raises(w.rc.RootCapError):
            w.register(contract_ref=w.tmp / "missing.md")
        w.artifact.write_text("The cap is frozen at 9.")
        with pytest.raises(w.rc.RootCapError):
            w.register()

    @pytest.mark.parametrize("owner", ["t_0000finished", "t_nonexistent"])
    def test_owner_must_be_an_open_card(self, w, owner):
        with pytest.raises(w.rc.RootCapError):
            w.register(owner_card=owner)

    def test_only_supported_key_and_existing_home(self, w):
        with pytest.raises(w.rc.RootCapError):
            w.register(key="display.skin")
        with pytest.raises(w.rc.RootCapError):
            w.register(target_home=w.tmp / "nowhere")

    def test_new_revision_supersedes_old(self, w):
        w.register()
        w.artifact.write_text(f"Root {KEY} is frozen at 9 (revision 2).")
        os.utime(w.artifact, (time.time() - 3000, time.time() - 3000))
        w.register(frozen_value=9)
        data = json.loads((w.shared / "root_cap_contracts.json").read_text())
        assert [d["active"] for d in data] == [False, True]


# ---------------------------------------------------------------- contract discovery / lifecycle
class TestContractScope:
    def test_no_contract_means_no_guard(self, w):
        assert not w.blocked("terminal", SET8)  # not a hardcoded 7

    def test_cross_session_discovery(self, w):
        w.register()
        assert w.blocked("terminal", SET8, session="another-chat")
        assert w.blocked("execute_code", SET8, session="third-chat")

    def test_profile_home_same_key_is_a_different_target(self, w, tmp_path):
        w.register()
        prof = w.root / "profiles" / "bb"
        prof.mkdir(parents=True)
        w.monkeypatch.setenv("HERMES_HOME", str(prof))
        assert not w.blocked("terminal", SET8)  # profile agent's own home is not the frozen root
        assert w.blocked("terminal", f"HERMES_HOME={w.root} {SET8}")
        assert w.blocked("terminal", f"hermes -p default config set {KEY} 8")
        assert not w.blocked("terminal", f"hermes -p other config set {KEY} 8")
        assert w.blocked("terminal", f"hermes --profile=default config set {KEY} 8")

    def test_other_root_home_is_not_frozen(self, w, tmp_path):
        w.register()
        other = tmp_path / "other-home"
        other.mkdir()
        assert not w.blocked("terminal", f"HERMES_HOME={other} {SET8}")

    def test_contract_lapses_when_owner_card_is_done(self, w):
        w.register()
        db = sqlite3.connect(w.kdb)
        db.execute("update tasks set status='done' where id=?", (OWNER,))
        db.commit()
        db.close()
        assert not w.blocked("terminal", SET8)

    def test_tampered_artifact_blocks_only_the_targeted_key(self, w):
        w.register()
        w.artifact.write_text("rewritten after approval")
        out = w.call("terminal", SET8, texts=[PERM])  # even an authorized change: the revision is unprovable
        assert out["action"] == "block" and "revision" in out["message"].lower()
        assert not w.blocked("terminal", "hermes config set display.skin default")
        assert not w.blocked("terminal", f"hermes config get {KEY}")

    def test_unreadable_kanban_is_ambiguous_not_global(self, w):
        w.register()
        w.kdb.write_bytes(b"not a database")
        assert w.blocked("terminal", SET8)
        assert w.blocked("terminal", SET8, texts=[PERM])
        assert not w.blocked("terminal", "hermes config set display.skin default")

    def test_two_active_contracts_with_different_values_are_ambiguous(self, w):
        w.register()
        p = w.shared / "root_cap_contracts.json"
        data = json.loads(p.read_text())
        data.append({**data[0], "frozen_value": 9, "contract_revision_digest": "x" * 64})
        p.write_text(json.dumps(data))
        assert w.blocked("terminal", f"hermes config set {KEY} 9")
        assert not w.blocked("terminal", "hermes config set display.skin default")

    def test_refusal_names_contract_and_queue_alternatives_without_leaking(self, w):
        w.register()
        out = w.call("terminal", "SECRET_TOKEN=hunter2 " + SET8)
        msg = out["message"]
        assert str(w.artifact) in msg or "spec.md" in msg
        assert "priority" in msg.lower() and "re-place" in msg.lower()
        assert "hunter2" not in msg
        log = (w.store.root / "log.jsonl").read_text()
        assert "hunter2" not in log and "root_cap" in log and KEY in log


# ---------------------------------------------------------------- native authority
class TestAuthority:
    def test_late_revocation_withdraws_earlier_authorization(self, w):
        w.register()
        assert w.blocked("terminal", SET8, texts=[PERM, f"Revoke that: do not change {KEY}."])

    def test_authorization_after_revocation_works(self, w):
        w.register()
        assert not w.blocked("terminal", SET8, texts=[f"Revoke my earlier {KEY} approval.", PERM])

    @pytest.mark.parametrize("text", [
        "Go ahead and do whatever you need.",
        "There is headroom, so raising it is fine.",
        f"Should I authorize changing root {KEY} from 7 to 8?",
        f"If needed I will authorize changing root {KEY} from 7 to 8.",
        f"I do not authorize changing root {KEY} from 7 to 8.",
        f"I authorize changing root {KEY} from 6 to 8.",      # a different revision
        f"I authorize changing root {KEY} from 7 to 9.",      # a different value
        f"The assistant said: authorize changing root {KEY} from 7 to 9. Not me.",
    ])
    def test_non_decisions_do_not_authorize(self, w, text):
        w.register()
        assert w.blocked("terminal", SET8, texts=[text])

    def test_authorized_value_only_covers_that_value(self, w):
        w.register()
        assert w.blocked("terminal", f"hermes config set {KEY} 9", texts=[PERM])

    def test_revision_digest_can_stand_in_for_from_value(self, w):
        rec = w.register()
        digest = rec["contract_revision_digest"][:12]
        assert not w.blocked("terminal", SET8, texts=[f"I authorize root {KEY} = 8 superseding contract revision {digest}."])

    def test_non_evidence_rows_are_not_user_authority(self, w):
        w.register()
        w.turn("s1", [QUEUE])
        rows = [{"role": "user", "content": PERM, "display_kind": "notice"}, {"role": "user", "content": QUEUE}]
        w.hooks["pre_llm_call"](session_id="s1", user_message=QUEUE, conversation_history=rows, platform="desktop")
        out = w.hooks["pre_tool_call"](session_id="s1", tool_name="terminal", args={"command": SET8})
        assert out and out["action"] == "block"

    def test_authority_of_another_chat_is_not_borrowed(self, w):
        w.register()
        w.turn("chat-a", [PERM])
        w.turn("chat-b", [QUEUE])
        out = w.hooks["pre_tool_call"](session_id="chat-b", tool_name="terminal", args={"command": SET8})
        assert out and out["action"] == "block"

    def test_permanent_supersession_retains_provenance_and_releases_freeze_after_readback(self, w):
        w.register()
        assert not w.blocked("terminal", SET8, texts=[PERM])
        recs = json.loads((w.shared / "root_cap_exceptions.json").read_text())
        assert recs[0]["kind"] == "permanent" and recs[0]["override_value"] == 8 and recs[0]["state"] == "admitted"
        # Contract is still active until the mutation is read back.
        assert json.loads((w.shared / "root_cap_contracts.json").read_text())[0]["active"] is True
        w.config.write_text("kanban:\n  max_in_progress: 8\n")  # the real write happened
        w.hooks["post_tool_call"](session_id="s1", tool_name="terminal", args={"command": SET8}, result="Set")
        recs = json.loads((w.shared / "root_cap_exceptions.json").read_text())
        assert recs[0]["state"] == "applied"
        contract = json.loads((w.shared / "root_cap_contracts.json").read_text())[0]
        assert contract["active"] is False and contract["superseded_by"]["decision_sha256"] == recs[0]["decision_sha256"]


# ---------------------------------------------------------------- temporary exceptions
class TestTemporary:
    def call_temp(self, w, text, command=SET8, surface="terminal"):
        w.register()
        return w.blocked(surface, command, texts=[text])

    def test_forged_owner_refused(self, w):
        assert self.call_temp(w, TEMP.replace(TEMP_OWNER, "t_0000forged0"))

    def test_finished_owner_refused(self, w):
        assert self.call_temp(w, TEMP.replace(TEMP_OWNER, "t_0000finished"))

    def test_expired_restore_refused(self, w):
        assert self.call_temp(w, TEMP.replace(FAR, "2001-01-01T00:00:00Z"))

    def test_restore_value_must_be_the_original(self, w):
        assert self.call_temp(w, TEMP.replace(f"Restore root {KEY} to 7", f"Restore root {KEY} to 9"))

    def test_unreadable_board_cannot_validate_the_owner(self, w):
        w.register()
        # contract stays provable (artifact) but the temp owner lives nowhere readable: kanban db replaced
        w.kdb.write_bytes(b"garbage")
        assert w.blocked("terminal", SET8, texts=[TEMP])

    def test_due_restores_reports_cas_state(self, w):
        w.register()
        assert not w.blocked("terminal", SET8, texts=[TEMP])
        w.config.write_text("kanban:\n  max_in_progress: 8\n")
        w.hooks["post_tool_call"](session_id="s1", tool_name="terminal", args={"command": SET8}, result="Set")
        assert w.rc.due_restores(w.store.root, now=time.time()) == []
        later = time.time() + 10 ** 10
        assert [d["action"] for d in w.rc.due_restores(w.store.root, now=later)] == ["restore"]
        w.config.write_text("kanban:\n  max_in_progress: 10\n")
        assert [d["action"] for d in w.rc.due_restores(w.store.root, now=later)] == ["conflict"]
        db = sqlite3.connect(w.kdb)
        db.execute("update tasks set status='done' where id=?", (TEMP_OWNER,))
        db.commit()
        db.close()
        assert w.rc.due_restores(w.store.root, now=time.time())[0]["action"] == "conflict"

    def test_simple_yaml_reader_matches_the_value(self, w):
        assert w.rc._simple_yaml_get("a:\n  b: 1\nkanban:\n  x: 2\n  max_in_progress: 8 # c\n", KEY) == 8
        assert w.rc._simple_yaml_get("kanban:\n  other: 1\n", KEY) is None

    def test_owner_terminal_only_trigger_is_enough(self, w):
        text = TEMP.replace(f" or at {FAR}, whichever is first", "")
        assert not self.call_temp(w, text)

    def test_expiry_only_trigger_is_enough(self, w):
        text = TEMP.replace("on owner completion or at", "at").replace(", whichever is first", "")
        assert not self.call_temp(w, text)

    def test_active_after_exact_readback(self, w):
        w.register()
        assert not w.blocked("terminal", SET8, texts=[TEMP])
        w.hooks["post_tool_call"](session_id="s1", tool_name="terminal", args={"command": SET8}, result="Set")
        # value still 7: mutation did not happen -> not marked active
        assert json.loads((w.shared / "root_cap_exceptions.json").read_text())[0]["state"] == "not_applied"
        w.config.write_text("kanban:\n  max_in_progress: 8\n")
        assert not w.blocked("terminal", SET8, texts=[TEMP], session="s2")
        w.hooks["post_tool_call"](session_id="s2", tool_name="terminal", args={"command": SET8}, result="Set")
        states = [r["state"] for r in json.loads((w.shared / "root_cap_exceptions.json").read_text())]
        assert states[-1] == "active"
        # temporary exception does not release the freeze
        assert json.loads((w.shared / "root_cap_contracts.json").read_text())[0]["active"] is True

    def test_restore_is_allowed_but_never_overwrites_a_newer_decision(self, w):
        w.register()
        assert not w.blocked("terminal", SET8, texts=[TEMP])
        w.config.write_text("kanban:\n  max_in_progress: 8\n")
        w.hooks["post_tool_call"](session_id="s1", tool_name="terminal", args={"command": SET8}, result="Set")
        assert not w.blocked("terminal", f"hermes config set {KEY} 7", texts=[QUEUE])  # exact restore (CAS: still 8)
        w.config.write_text("kanban:\n  max_in_progress: 10\n")  # a later user decision moved it on
        out = w.call("terminal", f"hermes config set {KEY} 7", texts=[QUEUE])
        assert out["action"] == "block" and "newer" in out["message"].lower()

    def test_persistence_failure_refuses_admission(self, w):
        w.register()
        (w.shared / "root_cap_exceptions.json").mkdir()  # cannot be written/read back
        assert w.blocked("terminal", SET8, texts=[TEMP])


# ---------------------------------------------------------------- parser: blocked vs data
BLOCKED_TERMINAL = [
    f"hermes config set {KEY} 8",
    f"  hermes   config   set   '{KEY}'   \"8\"  ",
    f"/home/ubuntu/.hermes/bin/hermes config set {KEY} 8",
    f"sudo -n hermes config set {KEY} 8",
    f"env FOO=1 nice -n 5 hermes config set {KEY} 8",
    f"FOO=1 hermes config set {KEY} 08",
    f"hermes config set {KEY} 8.0",
    f"hermes config set {KEY}=8",
    f"echo ok && hermes config set {KEY} 8",
    f"echo ok; hermes config set {KEY} 8 | tail -1",
    f"false || hermes config set {KEY} 8",
    f"(hermes config set {KEY} 8)",
    f"{{ hermes config set {KEY} 8; }}",
    f"echo $(hermes config set {KEY} 8)",
    f"echo `hermes config set {KEY} 8`",
    f"bash -lc 'hermes config set {KEY} 8'",
    f"sh -c \"cd /tmp && hermes config set {KEY} 8\"",
    f"eval \"hermes config set {KEY} 8\"",
    f"timeout 5 hermes config set {KEY} 8",
    f"hermes --profile default config set {KEY} 8",
    f"python3 -m hermes_cli.main config set {KEY} 8",
    f"hermes config set {KEY} eight",                 # unparseable value cannot match the freeze
    f"hermes config set {KEY} 8 \\\n  --quiet",
    f"hermes config set {KEY} 8 <<EOF\nignored\nEOF",
    f"python3 -c 'import os; os.system(\"hermes config set {KEY} 8\")'",
    f"sed -i 's/max_in_progress: 7/max_in_progress: 8/' {{root}}/config.yaml",
    f"yq -i '.kanban.max_in_progress = 8' {{root}}/config.yaml",
    f"echo 'max_in_progress: 8' >> {{root}}/config.yaml",
    f"hermes config set {KEY} 8 # trailing comment",
    f"hermes config unset {KEY}",
]
ALLOWED_TERMINAL = [
    f"echo 'hermes config set {KEY} 8'",
    f"echo \"hermes config set {KEY} 8\"",
    f"grep -n 'config set {KEY} 8' notes.txt",
    f"cat <<EOF\nhermes config set {KEY} 8\nEOF",
    f"git commit -m 'hermes config set {KEY} 8'",
    f"printf '%s\\n' \"hermes config set {KEY} 8\" > notes.txt",
    f"hermes config get {KEY}",
    f"hermes config set {KEY} 7",                    # restating the frozen value
    "hermes config set display.skin default",
    f"hermes config set kanban.other 8",
    f"hermes config set other.{KEY} 8",
    f"hermes config show --help",
    f"python3 -c 'print(\"hermes config set {KEY} 8\")'",
    f"grep max_in_progress {{root}}/config.yaml",
    f"cat {{root}}/config.yaml",
    f"sed -n '1,5p' {{root}}/config.yaml",
    f"sed -i 's/other: 1/other: 2/' {{root}}/config.yaml",
    "ls -la",
]


class TestParser:
    @pytest.mark.parametrize("cmd", BLOCKED_TERMINAL)
    def test_blocked_terminal_forms(self, w, cmd):
        w.register()
        assert w.blocked("terminal", cmd.replace("{root}", str(w.root))), cmd

    @pytest.mark.parametrize("cmd", ALLOWED_TERMINAL)
    def test_data_and_unrelated_forms_pass(self, w, cmd):
        w.register()
        assert not w.blocked("terminal", cmd.replace("{root}", str(w.root))), cmd

    def test_unparseable_quote_fails_closed_only_for_the_targeted_key(self, w):
        w.register()
        assert w.blocked("terminal", f"hermes config set {KEY} 8 'unterminated")
        assert not w.blocked("terminal", "echo 'unterminated")

    def test_unresolvable_home_variable_is_ambiguous(self, w):
        w.register()
        assert w.blocked("terminal", f"HERMES_HOME=$SOMEWHERE {SET8}")

    @pytest.mark.parametrize("code,expect", [
        (f"from hermes_tools import terminal\nterminal('hermes config set {KEY} 8')", True),
        (f"from hermes_tools import terminal as t\nt(command='hermes config set {KEY} 8')", True),
        (f"import hermes_tools\nhermes_tools.terminal('hermes config set {KEY} 8')", True),
        (f"cmd = 'hermes config set {KEY} 8'\nterminal(cmd)", True),
        (f"base = 'hermes config set '\nterminal(base + '{KEY} 8')", True),
        (f"terminal(f'hermes config set {KEY} 8')", True),
        (f"import subprocess\nsubprocess.run(['hermes','config','set','{KEY}','8'])", True),
        (f"import subprocess\nsubprocess.run('hermes config set {KEY} 8', shell=True)", True),
        (f"import os\nos.system('hermes config set {KEY} 8')", True),
        (f"v = input()\nterminal('hermes config set {KEY} ' + v)", True),   # dynamic exec + key: ambiguous
        (f"import yaml\ncfg = yaml.safe_load(open('{{root}}/config.yaml'))\ncfg['kanban']['max_in_progress'] = 8\nyaml.safe_dump(cfg, open('{{root}}/config.yaml','w'))", True),
        (f"print('hermes config set {KEY} 8')", False),
        (f"terminal('hermes config get {KEY}')", False),
        (f"terminal('echo hermes config set {KEY} 8')", False),
        (f"terminal('hermes config set {KEY} 7')", False),
        ("terminal('hermes config set display.skin default')", False),
        ("v = input()\nterminal('ls ' + v)", False),
        (f"x = '{KEY}'\nprint(x)", False),
        ("def broken(:\n", False),
    ])
    def test_execute_code_forms(self, w, code, expect):
        w.register()
        out = w.hooks["pre_tool_call"](session_id="s1", tool_name="execute_code",
                                       args={"code": code.replace("{root}", str(w.root))})
        assert bool(out and out.get("action") == "block") is expect, code

    def test_other_tools_are_ignored(self, w):
        w.register()
        assert w.hooks["pre_tool_call"](session_id="s1", tool_name="read_file", args={"path": SET8}) is None
        assert w.hooks["pre_tool_call"](session_id="s1", tool_name="terminal", args=None) is None
        assert w.hooks["pre_tool_call"](session_id="s1", tool_name="terminal", args={"command": 5}) is None


# ---------------------------------------------------------------- wiring
class TestWiring:
    def test_register_wires_pre_and_post_callbacks(self, tmp_path, monkeypatch):
        mod = _coach_mod()
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        seen = {}

        class Ctx:
            def register_hook(self, name, fn, *a, **k):
                seen.setdefault(name, []).append(fn)

            def __getattr__(self, name):
                return lambda *a, **k: None

        try:
            mod.register(Ctx())
        except Exception:
            pass
        assert "pre_tool_call" in seen and "post_tool_call" in seen

    def test_guard_runs_before_other_pre_tool_logic_and_is_failsafe(self, w):
        w.register()
        w.monkeypatch.setattr(w.mod._load("root_cap"), "check", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        # an internal guard fault must not break unrelated tool calls
        assert w.hooks["pre_tool_call"](session_id="s1", tool_name="terminal", args={"command": "ls"}) is None
        # ...while a targeted frozen write is refused under the same load/call fault (no cap=None fail-open)
        out = w.hooks["pre_tool_call"](session_id="s1", tool_name="terminal", args={"command": SET8})
        assert out and out["action"] == "block"

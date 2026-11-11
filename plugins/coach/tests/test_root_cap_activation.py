"""Approved-source activation, canonical approval authority and owner-visible recovery for the root-cap guard.

Closes the three findings of the exact-head review of fd3b161a:
  1. no activation adapter: an approved rollout freeze declared by explicit references (task-local descriptor) must
     protect without any manual projection step, across profile stores, with corruption/steering handled fail-closed;
  2. reported/quoted speech registered a contract: approval authority is a first-person native human approval, or one
     explicitly referenced ActionStore operation whose request/revision/scope are verified read-only;
  3. due/conflicting restores had no consumer: they now ride the owner's next coach `pre_llm_call` context, persisted
     and deduplicated.
Real registered coach callbacks, a real user-steps ActionStore, fixture homes only; nothing live is touched. The
descriptor is written directly (NOT through `declare_source`) so no projection exists before the first guarded call.
"""
import importlib.util
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

from test_root_cap_guard import (FAR, KEY, OWNER, PERM, QUEUE, SET8, SURFACES, TEMP, TEMP_OWNER, World, w,  # noqa: F401
                                 wrap)
from test_root_cap_hardening import _profile_hooks

STEPS = Path(__file__).resolve().parents[2] / "user-steps" / "core.py"
PHRASE = "Hub cap7 across boards/profiles"
OWNER_SESSION = "owner-session"
OWNER_REF = {"gatewayId": "test-gateway", "profile": "default", "storedSessionId": OWNER_SESSION}
OWNER_CARD_REF = {"gatewayId": "test-gateway", "profile": "default", "type": "kanban", "id": OWNER, "board": "default"}


def _steps():
    spec = importlib.util.spec_from_file_location("steps_core_for_rootcap", STEPS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def native_source(w, message_id, **over):
    src = {"source_id": "rollout-native", "target_home": str(w.root), "key": KEY, "frozen_value": 7,
           "contract_ref": str(w.artifact), "owner_card": OWNER,
           "authority": {"type": "native_message", "session": "approval-chat", "message_id": message_id}}
    src.update(over)
    return src


def write_sources(w, *sources, raw=None):
    w.shared.mkdir(parents=True, exist_ok=True)
    (w.shared / "root_cap_sources.json").write_text(raw if raw is not None else json.dumps(list(sources)))


def approve_native(w):
    return w.message(f"I approve freezing root {KEY} at 7 for the worker-first rollout ({w.artifact.name}).")


class Steps:
    """A real ActionStore with one approved request; the owner card is bound to the approving session."""

    def __init__(self, w, tmp_path, phrase=PHRASE, choice="approve", bound=False):
        """bound=False is the bare store-only record (no native user row, no owner sourceRef): never authority.
        bound=True adds what the real flow produces: scope.sourceRefs naming the owner card and the canonical user
        decision row the owning runtime admitted into the owner's (human-opened) chat."""
        self.w, self.mod, self.bound = w, _steps(), bound
        self.path = tmp_path / "actions.sqlite3"
        self.store = self.mod.ActionStore(self.path)
        kdb = sqlite3.connect(w.kdb)
        kdb.execute("alter table tasks add column session_id text")
        kdb.execute("update tasks set session_id=? where id=?", (OWNER_SESSION, OWNER))
        kdb.commit()
        kdb.close()
        r = dict(schemaVersion=2, owner=dict(OWNER_REF), requestId=str(uuid.uuid4()), revision=1, kind="approval",
                 title="Freeze the hub capacity", scope=dict(markdown=f"{phrase}. Worker-first rollout.", complete=True,
                                                             sourceRefs=[dict(OWNER_CARD_REF)] if bound else []),
                 limits=[dict(key="spend", value="none")], exclusions=["No training"],
                 outcomes=dict(approve="Authorize this scope only", revise="Keep blocked", decline="Cancel"),
                 choices=[dict(id=e, label=e.title(), effect=e) for e in ["approve", "revise", "decline"]],
                 aliases=[], state="open")
        self.request = self.store.create(OWNER_REF, r)
        self.operation = self.store.decide(OWNER_REF, dict(requestId=self.request["requestId"], expectedRevision=1,
                                                           choiceId=choice, revisionText="Needs changes" if choice == "revise" else None,
                                                           idempotencyKey=str(uuid.uuid4())))["operation"]
        self.native_id = None
        if bound:
            w.session(OWNER_SESSION)  # a human-opened (desktop) chat
            self.native_id = w.message(self.native_text(), session=OWNER_SESSION)

    def native_text(self):
        """Exactly what the owning runtime admits: user-steps PromptAdmission._params(op)['text']."""
        spec = importlib.util.spec_from_file_location("pd_for_rootcap", STEPS.with_name("prompt_delivery.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.PromptAdmission._params(self.operation)["text"]

    def source(self, **over):
        auth = {"type": "action_operation", "db": str(self.path), "operation_id": self.operation["operationId"],
                "request_id": self.request["requestId"], "revision": 1, "owner": dict(OWNER_REF)}
        if self.native_id is not None:
            auth["native_message_id"] = self.native_id
        auth.update(over.pop("authority", {}))
        src = native_source(self.w, 0, source_id="rollout-operation", authority=auth,
                            mapping={"phrase": PHRASE, "rationale": "reviewed: hub cap across boards is the root cap key",
                                     "reviewed_by": "test-reviewer"})
        src.update(over)
        return src

    def sql(self, statement, *params):
        c = sqlite3.connect(self.path)
        c.execute(statement, params)
        c.commit()
        c.close()


# ------------------------------------------------------------------------ 1. activation without registration
@pytest.mark.parametrize("surface", SURFACES)
class TestActivation:
    def test_declared_freeze_protects_without_any_registration(self, w, surface):
        write_sources(w, native_source(w, approve_native(w)))
        assert not (w.shared / "root_cap_contracts.json").exists()  # nothing was registered
        assert w.blocked(surface, SET8, texts=[QUEUE])
        proj = json.loads((w.shared / "root_cap_contracts.json").read_text())
        assert len(proj) == 1 and proj[0]["source_id"] == "rollout-native" and proj[0]["frozen_value"] == 7

    def test_unrelated_config_and_reads_stay_allowed(self, w, surface):
        write_sources(w, native_source(w, approve_native(w)))
        assert not w.blocked(surface, "hermes config set display.skin default")
        assert not w.blocked(surface, f"hermes config get {KEY}")
        assert not w.blocked(surface, f"hermes config set {KEY} 7")  # a no-op write to the frozen value

    def test_profile_store_meets_the_declared_root_freeze(self, w, tmp_path, monkeypatch, surface):
        write_sources(w, native_source(w, approve_native(w)))
        hooks, store = _profile_hooks(w, tmp_path, monkeypatch)
        assert not (store.root / "root_cap_sources.json").exists()
        out = hooks["pre_tool_call"](session_id="s9", tool_name=surface, args=wrap(
            surface, f"HERMES_HOME={w.root} hermes config set {KEY} 8"))
        assert isinstance(out, dict) and out.get("action") == "block"

    def test_no_descriptor_anywhere_is_no_contract(self, w, surface):
        approve_native(w)  # an approval row alone, with no explicit reference, freezes nothing (no prose scanning)
        assert not w.blocked(surface, SET8, texts=[QUEUE])

    def test_corrupt_descriptor_refuses_only_the_targeted_write(self, w, surface):
        write_sources(w, raw="{not json")
        assert w.blocked(surface, SET8, texts=[QUEUE])
        assert not w.blocked(surface, "hermes config set display.skin default")
        assert not w.blocked(surface, "ls -la")

    def test_malformed_descriptor_record_refuses_only_the_targeted_write(self, w, surface):
        src = native_source(w, approve_native(w))
        del src["frozen_value"]
        write_sources(w, src)
        assert w.blocked(surface, SET8, texts=[QUEUE])
        assert not w.blocked(surface, "hermes config set display.skin default")

    def test_unprovable_approval_keeps_the_freeze_known_and_refuses(self, w, surface):
        mid = w.message(f"I approve freezing root {KEY} at 7 ({w.artifact.name}).", session="worker-chat")  # machine chat
        write_sources(w, native_source(w, mid))
        out = w.call(surface, SET8, texts=[QUEUE])
        assert out and out["action"] == "block" and "cannot be verified" in out["message"]
        assert not w.blocked(surface, "hermes config set display.skin default")

    def test_missing_approval_row_refuses(self, w, surface):
        write_sources(w, native_source(w, 999))
        assert w.blocked(surface, SET8, texts=[QUEUE])

    def test_finished_owner_lapses_the_freeze(self, w, surface):
        write_sources(w, native_source(w, approve_native(w), owner_card="t_0000finished"))
        assert not w.blocked(surface, SET8, texts=[QUEUE])

    def test_edited_artifact_after_approval_is_ambiguous_and_refused(self, w, surface):
        write_sources(w, native_source(w, approve_native(w)))
        assert w.blocked(surface, SET8, texts=[QUEUE])  # projects revision 1
        w.artifact.write_text(w.artifact.read_text() + "\nA later silent edit.\n")
        assert w.blocked(surface, SET8, texts=[QUEUE])

    def test_genuine_user_authorization_still_changes_it_and_is_not_revived(self, w, surface):
        write_sources(w, native_source(w, approve_native(w)))
        w.session("s-human")
        assert not w.blocked(surface, SET8, session="s-human", texts=[PERM])
        w.config.write_text(f"kanban:\n  max_in_progress: 8\n  other: 1\n")
        w.hooks["post_tool_call"](session_id="s-human", tool_name=surface, args=wrap(surface, SET8), result="ok")
        recs = json.loads((w.shared / "root_cap_contracts.json").read_text())
        assert recs[0]["active"] is False and recs[0]["superseded_by"]["value"] == 8
        # the descriptor must not revive a superseded freeze
        assert not w.blocked(surface, f"hermes config set {KEY} 9", texts=[QUEUE])

    def test_queue_notice_after_declaration_is_still_not_authority(self, w, surface):
        write_sources(w, native_source(w, approve_native(w)))
        assert w.blocked(surface, SET8, texts=[QUEUE, "Bump priority, re-place, or do the work here."])


def test_declare_source_verifies_now_and_refuses_unprovable(w):
    with pytest.raises(w.rc.RootCapError):
        w.rc.declare_source(None, target_home=w.root, key=KEY, frozen_value=7, contract_ref=w.artifact,
                            owner_card=OWNER, authority={"type": "native_message", "session": "approval-chat",
                                                         "message_id": 999})
    rec = w.rc.declare_source(None, target_home=w.root, key=KEY, frozen_value=7, contract_ref=w.artifact,
                              owner_card=OWNER, authority={"type": "native_message", "session": "approval-chat",
                                                           "message_id": approve_native(w)})
    assert json.loads((w.shared / "root_cap_sources.json").read_text())[0]["source_id"] == rec["source_id"]
    assert w.blocked("terminal", SET8, texts=[QUEUE])


def test_declare_cli_writes_the_root_descriptor(w, capsys):
    mid = approve_native(w)
    rc = w.rc.main(["declare", "--target-home", str(w.root), "--value", "7", "--contract-ref", str(w.artifact),
                    "--owner-card", OWNER, "--approval-session", "approval-chat", "--approval-message", str(mid)])
    assert rc == 0 and (w.shared / "root_cap_sources.json").exists()
    assert w.rc.main(["declare", "--target-home", str(w.root), "--value", "7", "--contract-ref", str(w.artifact),
                      "--owner-card", OWNER, "--approval-session", "approval-chat", "--approval-message", "999"]) == 1


# ------------------------------------------------------------------------ 2. canonical approval authority
@pytest.mark.parametrize("surface", SURFACES)
class TestActionOperationAuthority:
    def test_explicit_operation_reference_protects_without_registration(self, w, tmp_path, surface):
        st = Steps(w, tmp_path, bound=True)
        write_sources(w, st.source())
        assert w.blocked(surface, SET8, texts=[QUEUE])
        assert (w.shared / "root_cap_contracts.json").exists()

    def test_legacy_and_bound_rows_are_both_verified(self, w, tmp_path, surface):
        st = Steps(w, tmp_path, bound=True)
        assert "binding" in st.operation  # v1 bound row: scopeDigest is checked
        st.sql("update operations set body=?, id=id where id=?",
               json.dumps({k: v for k, v in st.operation.items() if k != "binding"}), st.operation["operationId"])
        write_sources(w, st.source())
        assert w.blocked(surface, SET8, texts=[QUEUE])  # legacy-unbound accepted on the equalities, still frozen

    def test_genuine_user_authorization_still_possible(self, w, tmp_path, surface):
        st = Steps(w, tmp_path, bound=True)
        write_sources(w, st.source())
        w.session("s-human")
        assert not w.blocked(surface, SET8, session="s-human", texts=[PERM])


@pytest.mark.parametrize("mutate,why", [
    pytest.param(lambda st: st.sql("update requests set state='withdrawn' where id=?", st.request["requestId"]),
                 "withdrawn", id="withdrawn"),
    pytest.param(lambda st: st.sql("update requests set revision=2 where id=?", st.request["requestId"]),
                 "superseded", id="superseded-revision"),
    pytest.param(lambda st: st.sql("update requests set resolution=null where id=?", st.request["requestId"]),
                 "resolution", id="no-resolution"),
    pytest.param(lambda st: st.sql("delete from operations where id=?", st.operation["operationId"]),
                 "not found", id="operation-missing"),
    pytest.param(lambda st: st.sql("update revisions set body=replace(body, 'Worker-first', 'Changed') where id=?",
                                   st.request["requestId"]), "differs from the stored revision", id="intent-differs"),
])
def test_operation_conflicts_refuse_the_targeted_write_with_the_exact_reason(w, tmp_path, mutate, why):
    st = Steps(w, tmp_path, bound=True)
    mutate(st)
    write_sources(w, st.source())
    out = w.call("terminal", SET8, texts=[QUEUE])
    assert out and out["action"] == "block" and why in out["message"], out
    assert not w.blocked("terminal", "hermes config set display.skin default")


@pytest.mark.parametrize("name,kwargs", [
    pytest.param("revise", {"choice": "revise"}, id="not-an-approval"),
    pytest.param("decline", {"choice": "decline"}, id="decline"),
    pytest.param("phrase", {"phrase": "Some other scope"}, id="unrelated-scope-wording"),
])
def test_operation_that_is_not_this_approval_is_refused(w, tmp_path, name, kwargs):
    st = Steps(w, tmp_path, bound=True, **kwargs)
    src = st.source()
    if name == "phrase":
        src["mapping"]["phrase"] = PHRASE  # the request says something else
    write_sources(w, src)
    assert w.blocked("terminal", SET8, texts=[QUEUE])


def test_wrong_owner_namespace_or_session_is_refused(w, tmp_path):
    st = Steps(w, tmp_path, bound=True)
    for patch in ({"owner": {**OWNER_REF, "profile": "someone-else"}}, {"owner": {**OWNER_REF, "gatewayId": "other"}},
                  {"owner": {**OWNER_REF, "storedSessionId": "another-session"}}):
        write_sources(w, st.source(authority=patch))
        assert w.blocked("terminal", SET8, texts=[QUEUE]), patch


def test_owner_card_bound_to_another_session_is_refused(w, tmp_path):
    st = Steps(w, tmp_path, bound=True)
    kdb = sqlite3.connect(w.kdb)
    kdb.execute("update tasks set session_id='different' where id=?", (OWNER,))
    kdb.commit()
    kdb.close()
    write_sources(w, st.source())
    out = w.call("terminal", SET8, texts=[QUEUE])
    assert out and "owner card's session" in out["message"]


def test_tampered_scope_digest_of_a_bound_row_is_refused(w, tmp_path):
    st = Steps(w, tmp_path, bound=True)
    op = dict(st.operation)
    op["binding"] = {**op["binding"], "scopeDigest": "0" * 64}
    st.sql("update operations set body=? where id=?", json.dumps(op), op["operationId"])
    write_sources(w, st.source())
    out = w.call("terminal", SET8, texts=[QUEUE])
    assert out and "scope digest" in out["message"]


def test_operation_descriptor_without_a_reviewed_mapping_is_malformed(w, tmp_path):
    st = Steps(w, tmp_path, bound=True)
    src = st.source()
    del src["mapping"]
    write_sources(w, src)
    assert w.blocked("terminal", SET8, texts=[QUEUE])


# ------------------------------------------------------------------------ 2b. registration authority
class TestRegistrationRejectsReportedSpeech:
    @pytest.mark.parametrize("text", [
        f'The assistant wrote: "I approve freezing root {KEY} at 7 (spec.md)."',
        f"> I approve freezing root {KEY} at 7 (spec.md).",
        f'He said "I approve freezing root {KEY} at 7 (spec.md)".',
        f'"I approve freezing root {KEY} at 7 (spec.md)."',
        f"The notice reads: I approve freezing root {KEY} at 7 (spec.md).",
    ])
    def test_quoted_or_reported_approval_cannot_register(self, w, text):
        with pytest.raises(w.rc.RootCapError):
            w.register(approval_message_id=w.message(text))
        assert not (w.shared / "root_cap_contracts.json").exists()

    def test_quoted_approval_cannot_replace_an_active_projection(self, w):
        w.register()
        before = (w.shared / "root_cap_contracts.json").read_text()
        w.artifact.write_text(w.artifact.read_text() + "\nrev 2\n")
        with pytest.raises(w.rc.RootCapError):
            w.register(approval_message_id=w.message(f'The assistant wrote: "I approve freezing root {KEY} at 7 (spec.md)."'))
        assert (w.shared / "root_cap_contracts.json").read_text() == before

    @pytest.mark.parametrize("text", [
        f"I approve freezing root {KEY} at 7 (spec.md).",
        f"I approve freezing root `{KEY}` at 7 (spec.md).",
        f"Approved: freeze root {KEY} at 7 per spec.md.",
    ])
    def test_genuine_first_person_approval_still_registers(self, w, text):
        assert w.register(approval_message_id=w.message(text))["frozen_value"] == 7

    def test_approval_from_a_machine_session_cannot_register(self, w):
        with pytest.raises(w.rc.RootCapError):
            w.register(approval_session="worker-chat", approval_message_id=w.message(
                f"I approve freezing root {KEY} at 7 (spec.md).", session="worker-chat"))


# ------------------------------------------------------------------------ 3. owner-visible recovery notice
class TestRecoveryNotice:
    def _active_temp(self, w):
        w.register()
        assert not w.blocked("terminal", SET8, texts=[TEMP], session="s1")
        w.config.write_text("kanban:\n  max_in_progress: 8\n")
        w.hooks["post_tool_call"](session_id="s1", tool_name="terminal", args={"command": SET8}, result="ok")

    def _later(self, w):
        real = time.time
        w.monkeypatch.setattr(w.rc.time, "time", lambda: real() + 10 ** 10)

    def _turn(self, w, session, hooks=None):
        return (hooks or w.hooks)["pre_llm_call"](session_id=session, user_message="hi", conversation_history=[],
                                                 platform="desktop")

    def test_owner_session_receives_the_restore_instruction_once(self, w):
        self._active_temp(w)
        self._later(w)
        out = self._turn(w, "s1")
        assert out and "root-cap recovery due" in out["context"] and TEMP_OWNER in out["context"]
        assert f"hermes config set {KEY} 7" in out["context"]
        again = self._turn(w, "s1") or {}
        assert "root-cap recovery" not in again.get("context", "")
        assert "root_cap_restore_due" in (w.store.root / "log.jsonl").read_text()

    def test_delivery_is_persisted_across_a_restart(self, w, tmp_path, monkeypatch):
        self._active_temp(w)
        self._later(w)
        assert "recovery due" in self._turn(w, "s1")["context"]
        _, hooks2 = w.mod.build(store=w.store, checkout=None)
        again = self._turn(w, "s1", hooks2) or {}
        assert "root-cap recovery" not in again.get("context", "")

    def test_another_live_chat_gets_one_fallback_then_the_owner_chat_still_gets_its_own(self, w):
        self._active_temp(w)
        self._later(w)
        other = self._turn(w, "s-other")
        assert other and "recovery due" in other["context"]
        assert "root-cap recovery" not in (self._turn(w, "s-third") or {}).get("context", "")
        assert "recovery due" in self._turn(w, "s1")["context"]
        assert "root-cap recovery" not in (self._turn(w, "s1") or {}).get("context", "")

    def test_conflict_is_reported_as_do_not_overwrite(self, w):
        self._active_temp(w)
        p = w.shared / "root_cap_contracts.json"
        data = json.loads(p.read_text())
        data[0]["active"] = False
        p.write_text(json.dumps(data))
        self._later(w)
        out = self._turn(w, "s1")
        assert out and "CONFLICT" in out["context"] and "Do NOT overwrite" in out["context"]

    def test_nothing_due_means_no_notice(self, w):
        self._active_temp(w)
        assert "root-cap recovery" not in (self._turn(w, "s1") or {}).get("context", "")

    def test_notice_survives_an_unrelated_hook_fault(self, w):
        self._active_temp(w)
        self._later(w)
        w.monkeypatch.setattr(w.mod, "_load", w.mod._load)  # keep real loader
        w.store.user_turn = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        out = self._turn(w, "s1")
        assert out and "recovery due" in out["context"]

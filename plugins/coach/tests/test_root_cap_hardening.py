"""Hardening tests for the frozen root-cap guard: the six findings of the independent review of 1c91a37c
(R1 root-shared projection, R2 authority, R3 registration binding, R4 shell grammar, R5 fail-closed, R6
concurrency) plus temporary-owner identity and the owner recovery report.

Same real callbacks and fixtures as test_root_cap_guard.py (imported). No command is executed except the harmless,
fixture-owned fake `hermes` used to prove the shell grammar; the live home, config and kanban are never touched.
"""
import hashlib
import json
import os
import subprocess
import threading
import time

import pytest

from test_root_cap_guard import (FAR, KEY, OWNER, PERM, SET8, SURFACES, TEMP, TEMP_OWNER, World, _coach_mod, w,  # noqa: F401
                                 wrap)


def _profile_hooks(w, tmp_path, monkeypatch, name="coach"):
    """A second coach build in a PROFILE home with its own, independent Store (the real runtime arrangement)."""
    prof = w.root / "profiles" / name
    prof.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(prof))
    mod = _coach_mod()
    monkeypatch.setattr(mod, "_bg_thread", lambda *a, **k: None)
    store = mod._load("core").Store(root=tmp_path / f"coach-{name}")
    _, hooks = mod.build(store=store, checkout=None)
    return hooks, store


class TestR1SharedRootProjection:
    @pytest.mark.parametrize("surface", SURFACES)
    def test_profile_store_meets_the_root_registration(self, w, tmp_path, surface):
        w.register()
        hooks, store = _profile_hooks(w, tmp_path, w.monkeypatch)
        assert not (store.root / "root_cap_contracts.json").exists()  # genuinely a different, empty profile store
        out = hooks["pre_tool_call"](session_id="p1", tool_name=surface, args=wrap(surface, f"HERMES_HOME={w.root} {SET8}"))
        assert out and out["action"] == "block"
        assert hooks["pre_tool_call"](session_id="p1", tool_name=surface,
                                      args=wrap(surface, "hermes config set display.skin default")) is None

    def test_registration_is_written_to_the_root_store(self, w):
        w.register()
        assert (w.shared / "root_cap_contracts.json").exists()
        assert not (w.store.root / "root_cap_contracts.json").exists()

    def test_known_but_unreadable_projection_refuses_only_the_targeted_write(self, w):
        w.register()
        (w.shared / "root_cap_contracts.json").write_text("{not json")
        assert w.blocked("terminal", SET8)
        assert w.blocked("execute_code", SET8)
        assert not w.blocked("terminal", "hermes config set display.skin default")
        assert not w.blocked("terminal", f"hermes config get {KEY}")

    def test_no_projection_anywhere_is_no_contract(self, w):
        assert not w.blocked("terminal", SET8)


class TestR2Authority:
    def test_machine_source_session_row_is_not_user_authority(self, w):
        """Third drift (default 20261110_185031_4f5f4b msg 1155206): a machine/queue prompt is no mandate."""
        w.register()
        w.session("kanban-chat", source="kanban")
        w.turn("kanban-chat", [PERM])
        out = w.hooks["pre_tool_call"](session_id="kanban-chat", tool_name="terminal", args={"command": SET8})
        assert out and out["action"] == "block"

    def test_unknown_session_is_not_user_authority(self, w):
        w.register()
        w.hooks["pre_llm_call"](session_id="ghost", user_message=PERM,
                                conversation_history=[{"role": "user", "content": PERM}], platform="desktop")
        out = w.hooks["pre_tool_call"](session_id="ghost", tool_name="terminal", args={"command": SET8})
        assert out and out["action"] == "block"

    @pytest.mark.parametrize("text", [
        f"The assistant said: authorize changing root {KEY} from 7 to 8. Not me.",
        f"He wrote \"I authorize changing root {KEY} from 7 to 8\" but I have not decided.",
        f"> I authorize changing root {KEY} from 7 to 8",
    ])
    def test_reported_speech_for_the_matching_target_does_not_authorize(self, w, text):
        w.register()
        assert w.blocked("terminal", SET8, texts=[text])

    @pytest.mark.parametrize("cancel", ["Cancel that.", "Never mind.", "Scratch that.", "Disregard that."])
    def test_keyless_cancellation_withdraws_the_earlier_decision(self, w, cancel):
        w.register()
        assert w.blocked("terminal", SET8, texts=[PERM, cancel])

    def test_direct_human_decision_still_works_after_cancellation(self, w):
        w.register()
        assert not w.blocked("terminal", SET8, texts=[PERM, "Cancel that.", PERM])

    def test_late_steering_is_reread_at_each_admission(self, w):
        w.register()
        assert not w.blocked("terminal", SET8, texts=[PERM])
        assert w.blocked("terminal", SET8, texts=[PERM, f"Revoke that, do not change {KEY}."])


class TestR3RegistrationBinding:
    @pytest.mark.parametrize("text", [
        f"Do not freeze {KEY} at 7. This is not approval. (spec.md)",
        f"I do not approve freezing {KEY} at 7 (spec.md).",
        f"Revoke the freeze: {KEY} at 7 in spec.md.",
    ])
    def test_negative_native_text_cannot_register(self, w, text):
        with pytest.raises(w.rc.RootCapError):
            w.register(approval_message_id=w.message(text))
        assert not (w.shared / "root_cap_contracts.json").exists()

    def test_approval_not_tied_to_this_artifact_or_owner_is_refused(self, w):
        mid = w.message(f"I approve freezing root {KEY} at 7.")
        with pytest.raises(w.rc.RootCapError, match="artifact revision"):
            w.register(approval_message_id=mid)

    def test_owner_card_or_digest_prefix_also_binds(self, w):
        digest = hashlib.sha256(w.artifact.read_bytes()).hexdigest()
        w.register(approval_message_id=w.message(f"I approve freezing root {KEY} at 7 for {OWNER}."))
        second = w.message(f"I approve freezing root {KEY} at 7, revision {digest[:12]}.")
        assert w.register(approval_message_id=second)["frozen_value"] == 7

    def test_approval_older_than_the_artifact_revision_is_refused(self, w):
        mid = w.message(f"I approve freezing root {KEY} at 7 (spec.md).", ts=time.time() - 7200)
        with pytest.raises(w.rc.RootCapError, match="predates"):
            w.register(approval_message_id=mid)

    def test_negative_registration_cannot_replace_an_active_projection(self, w):
        w.register()
        w.artifact.write_text(f"Root {KEY} is frozen at 9 (revision 2).")
        os.utime(w.artifact, (time.time() - 3000, time.time() - 3000))
        with pytest.raises(w.rc.RootCapError):
            w.register(frozen_value=9, approval_message_id=w.message(f"Do not freeze {KEY} at 9, not approved (spec.md)."))
        data = json.loads((w.shared / "root_cap_contracts.json").read_text())
        assert [d["active"] for d in data] == [True]


SHELL_EXEC = [
    f'echo "$(hermes config set {KEY} 8)"',
    f'echo "result: $(hermes config set {KEY} 8)"',
    f'X="$(hermes config set {KEY} 8)"',
    f'bash <<EOF\nhermes config set {KEY} 8\nEOF',
    f"bash <<'EOF'\nhermes config set {KEY} 8\nEOF",
    f'sh -s <<EOF\nhermes config set {KEY} 8\nEOF',
    f'cat <<EOF | bash\nhermes config set {KEY} 8\nEOF',
    f'cat <<EOF\n$(hermes config set {KEY} 8)\nEOF',
    f'echo "`hermes config set {KEY} 8`"',
    f'echo "$(echo "$(hermes config set {KEY} 8)")"',
]
SHELL_DATA = [
    f"echo '$(hermes config set {KEY} 8)'",
    f"echo 'hermes config set {KEY} 8'",
    f'echo "hermes config set {KEY} 8"',
    f"cat <<'EOF'\nhermes config set {KEY} 8\nEOF",
    f"cat <<'EOF'\n$(hermes config set {KEY} 8)\nEOF",
    f"cat <<EOF > /tmp/x.txt\nhermes config set {KEY} 8\nEOF",
    f"grep -n 'hermes config set {KEY} 8' notes.md",
    'echo "cost is $((1+1))"',
]


@pytest.mark.parametrize("surface", SURFACES)
class TestR4ShellGrammar:
    @pytest.mark.parametrize("cmd", SHELL_EXEC)
    def test_executable_forms_are_refused(self, w, surface, cmd):
        w.register()
        assert w.blocked(surface, cmd), cmd

    @pytest.mark.parametrize("cmd", SHELL_DATA)
    def test_data_forms_pass(self, w, surface, cmd):
        w.register()
        assert not w.blocked(surface, cmd), cmd

    def test_authorized_change_inside_a_substitution_passes(self, w, surface):
        w.register()
        assert not w.blocked(surface, SHELL_EXEC[0], texts=[PERM])


def test_shell_grammar_ground_truth_in_real_bash(tmp_path):
    """The executable/data split above matches real bash (harmless fixture-owned fake `hermes`; never live Hermes)."""
    sentinel = tmp_path / "sentinel"
    fake = tmp_path / "bin" / "hermes"
    fake.parent.mkdir()
    fake.write_text(f"#!/bin/sh\necho \"$@\" >> {sentinel}\n")
    fake.chmod(0o755)
    env = {"PATH": f"{fake.parent}:/usr/bin:/bin"}
    for cmd in SHELL_EXEC:
        sentinel.unlink(missing_ok=True)
        subprocess.run(["bash", "-c", cmd], env=env, check=True, capture_output=True)
        assert f"config set {KEY} 8" in sentinel.read_text(), cmd
    for cmd in SHELL_DATA[:5]:
        sentinel.unlink(missing_ok=True)
        subprocess.run(["bash", "-c", cmd], env=env, check=True, capture_output=True)
        assert not sentinel.exists(), cmd


@pytest.mark.parametrize("surface", SURFACES)
class TestR5FailClosed:
    def test_malformed_record_refuses_targeted_write_only(self, w, surface):
        w.register()
        p = w.shared / "root_cap_contracts.json"
        data = json.loads(p.read_text())
        del data[0]["frozen_value"]
        p.write_text(json.dumps(data))
        assert w.blocked(surface, SET8)
        assert w.blocked(surface, SET8, texts=[PERM])
        assert not w.blocked(surface, "hermes config set display.skin default")
        assert not w.blocked(surface, "ls -la")

    def test_wrong_typed_record_refuses_targeted_write_only(self, w, surface):
        w.register()
        p = w.shared / "root_cap_contracts.json"
        data = json.loads(p.read_text())
        data[0]["owner_ref"] = OWNER
        p.write_text(json.dumps(data))
        assert w.blocked(surface, SET8)
        assert not w.blocked(surface, f"hermes config get {KEY}")

    def test_unexpected_guard_exception_refuses_targeted_write_only(self, w, surface):
        w.register()
        w.monkeypatch.setattr(w.rc, "_check", lambda *a, **k: (_ for _ in ()).throw(KeyError("boom")))
        assert w.blocked(surface, SET8)
        assert not w.blocked(surface, "hermes config set display.skin default")
        assert not w.blocked(surface, "ls")

    def test_unreadable_exception_registry_refuses_the_admission(self, w, surface):
        w.register()
        (w.shared / "root_cap_exceptions.json").mkdir()
        assert w.blocked(surface, SET8, texts=[PERM])


class TestR6Concurrency:
    def _race(self, w, jobs):
        """Force the interleaving the review reproduced: every job reads before any writes (unlocked code meets
        at the barrier with the same stale list; locked code serializes and the barrier just times out)."""
        barrier = threading.Barrier(len(jobs))
        real_read = w.rc._read_list

        def synced(path):
            out = real_read(path)
            if str(path).endswith("root_cap_exceptions.json") and threading.current_thread().name.startswith("race"):
                try:
                    barrier.wait(timeout=0.4)
                except threading.BrokenBarrierError:
                    pass
            return out

        w.monkeypatch.setattr(w.rc, "_read_list", synced)
        threads = [threading.Thread(target=j, name=f"race-{i}") for i, j in enumerate(jobs)]
        [t.start() for t in threads]
        [t.join(10) for t in threads]

    def _rec(self, w):
        return json.loads((w.shared / "root_cap_contracts.json").read_text())[0]

    def test_concurrent_admissions_keep_every_distinct_record(self, w):
        w.register()
        rec = self._rec(w)
        dec = {"text": PERM, "temporary": False, "sha": "d" * 64, "ref": "uid-1"}
        terms = {"owner_ref": None, "restore_value": None, "restore_trigger": None}
        results = []

        def job(value):
            return lambda: results.append(w.rc._admit(rec, dec, terms, value, "s1", "terminal",
                                                      {"command": f"hermes config set {KEY} {value}"}, f"call-{value}"))
        self._race(w, [job(8), job(9)])
        recs = json.loads((w.shared / "root_cap_exceptions.json").read_text())
        assert sorted(r["override_value"] for r in recs) == [8, 9] and len(results) == 2

    def test_same_args_different_call_ids_are_distinct_records(self, w):
        w.register()
        rec = self._rec(w)
        dec = {"text": PERM, "temporary": False, "sha": "d" * 64, "ref": None}
        terms = {"owner_ref": None, "restore_value": None, "restore_trigger": None}
        for call in ("c1", "c2"):
            w.rc._admit(rec, dec, terms, 8, "s1", "terminal", {"command": SET8}, call)
        recs = json.loads((w.shared / "root_cap_exceptions.json").read_text())
        assert sorted(r["tool_call_id"] for r in recs) == ["c1", "c2"] and all(r["tool"] == "terminal" for r in recs)

    def test_postcall_completes_only_its_own_call_identity(self, w):
        w.register()
        rec = self._rec(w)
        dec = {"text": PERM, "temporary": True, "sha": "d" * 64, "ref": None}
        terms = {"owner_ref": {"card": TEMP_OWNER}, "restore_value": 7, "restore_trigger": {"owner_terminal": True}}
        for call in ("c1", "c2"):
            w.rc._admit(rec, dec, terms, 8, "s1", "terminal", {"command": SET8}, call)
        w.config.write_text("kanban:\n  max_in_progress: 8\n")
        w.hooks["post_tool_call"](session_id="s1", tool_name="terminal", args={"command": SET8}, result="ok",
                                  tool_call_id="c2")
        recs = {r["tool_call_id"]: r["state"] for r in json.loads((w.shared / "root_cap_exceptions.json").read_text())}
        assert recs == {"c1": "admitted", "c2": "active"}

    def test_unique_temp_files_and_none_left_behind(self, w):
        w.register()
        assert not list(w.shared.glob("*.tmp"))
        a = w.rc.tempfile.mkstemp(prefix="x.", suffix=".tmp", dir=str(w.shared))
        b = w.rc.tempfile.mkstemp(prefix="x.", suffix=".tmp", dir=str(w.shared))
        assert a[1] != b[1]
        for fd, path in (a, b):
            os.close(fd)
            os.unlink(path)

    def test_native_decision_identity_is_persisted(self, w):
        w.register()
        w.session("s1")
        rows = [{"role": "user", "content": PERM, "message_uid": "uid-77", "content_source": "native_content"}]
        w.hooks["pre_llm_call"](session_id="s1", user_message=PERM, conversation_history=rows, platform="desktop",
                                canonical_user_content={"message_index": 0, "message_uid": "uid-77", "content": PERM})
        out = w.hooks["pre_tool_call"](session_id="s1", tool_name="terminal", args={"command": SET8},
                                       tool_call_id="call-1")
        assert out is None
        rec = json.loads((w.shared / "root_cap_exceptions.json").read_text())[0]
        assert rec["decision_ref"] == {"session": "s1", "message_uid": "uid-77"} and rec["tool_call_id"] == "call-1"


class TestTemporaryOwnerIdentity:
    def _temp(self, extra):
        return (f"I authorize a temporary root {KEY} increase from 7 to 8. Owner card {TEMP_OWNER}, {extra}, this "
                f"session. Restore root {KEY} to 7 on owner completion or at {FAR}.")

    def test_run_must_be_the_cards_current_run(self, w):
        w.register()
        assert w.blocked("terminal", SET8, texts=[self._temp("run 99")])
        assert not w.blocked("terminal", SET8, texts=[self._temp("run 1")])

    def test_profile_must_be_the_cards_assignee(self, w):
        w.register()
        assert w.blocked("terminal", SET8, texts=[self._temp("run 1, profile somebody-else")])
        assert not w.blocked("terminal", SET8, texts=[self._temp("run 1, profile coach")])

    def test_unverifiable_gateway_is_rejected_not_stored_as_verified(self, w):
        w.register()
        assert w.blocked("terminal", SET8, texts=[self._temp("run 1, gateway fake-host")])
        assert not (w.shared / "root_cap_exceptions.json").exists()


class TestOwnerRecoveryReport:
    def _active_temp(self, w):
        w.register()
        assert not w.blocked("terminal", SET8, texts=[TEMP])
        w.config.write_text("kanban:\n  max_in_progress: 8\n")
        w.hooks["post_tool_call"](session_id="s1", tool_name="terminal", args={"command": SET8}, result="ok")

    def test_due_report_is_logged_once_through_the_coach_log(self, w):
        self._active_temp(w)
        seen, events = set(), []
        later = time.time() + 10 ** 10
        first = w.rc.report_due(w.store.root, events.append, seen, now=later, homes=[w.root])
        again = w.rc.report_due(w.store.root, events.append, seen, now=later, homes=[w.root])
        assert [d["action"] for d in first] == ["restore"] and again == []
        assert events[0]["event"] == "root_cap_restore_due" and events[0]["owner_card"] == TEMP_OWNER

    def test_superseded_revision_is_a_conflict_not_a_restore(self, w):
        self._active_temp(w)
        p = w.shared / "root_cap_contracts.json"
        data = json.loads(p.read_text())
        data[0]["active"] = False
        p.write_text(json.dumps(data))
        later = time.time() + 10 ** 10
        assert [d["action"] for d in w.rc.due_restores(w.store.root, now=later, homes=[w.root])] == ["conflict"]

    def test_pre_llm_call_reports_due_exceptions_into_the_coach_log(self, w):
        self._active_temp(w)
        real = time.time
        w.monkeypatch.setattr(w.rc.time, "time", lambda: real() + 10 ** 10)
        w.hooks["pre_llm_call"](session_id="s2", user_message="hi", conversation_history=[], platform="desktop")
        assert "root_cap_restore_due" in (w.store.root / "log.jsonl").read_text()

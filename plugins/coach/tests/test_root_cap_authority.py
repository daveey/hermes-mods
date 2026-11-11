"""Authority boundaries of the root-cap guard (v4): closes the three findings of the exact-head review of 6c16ccd4.

  1. the LATEST relevant human decision wins before its value is compared with the attempted write (a later explicit
     9 must not leave an older authorization of 8 admissible), on both callback shapes;
  2. a question or single-quoted text is not an approval (registration), while first-person approvals still register;
  3. an explicitly referenced ActionStore operation is authority only with its canonical native user row (the row the
     owning runtime admitted into the owner's human chat, embedding the same frozen request) AND a scope.sourceRefs
     entry naming the owner card; the legacy (unbound) operation shape is still accepted when those equalities hold.
Fixture homes only; nothing live is touched.
"""
import json
import sqlite3

import pytest

from test_root_cap_guard import KEY, OWNER, PERM, QUEUE, SET8, SURFACES, w  # noqa: F401
from test_root_cap_activation import OWNER_SESSION, Steps, write_sources, native_source  # noqa: F401

SET9 = f"hermes config set {KEY} 9"
TO9 = f"I authorize changing root {KEY} from 7 to 9 permanently for this rollout."


# ------------------------------------------------------------------------ 1. latest decision wins
@pytest.mark.parametrize("surface", SURFACES)
class TestLatestDecisionWins:
    def test_newer_other_value_supersedes_the_older_authorization(self, w, surface):
        w.register()
        assert w.blocked(surface, SET8, texts=[PERM, TO9])
        assert not w.blocked(surface, SET9, texts=[PERM, TO9])

    def test_newer_value_8_supersedes_older_9(self, w, surface):
        w.register()
        assert w.blocked(surface, SET9, texts=[TO9, PERM])
        assert not w.blocked(surface, SET8, texts=[TO9, PERM])

    def test_cancellation_after_the_latest_decision_refuses_every_value(self, w, surface):
        w.register()
        assert w.blocked(surface, SET8, texts=[PERM, TO9, "Cancel that."])
        assert w.blocked(surface, SET9, texts=[PERM, TO9, "Cancel that."])

    def test_single_decision_still_admits_and_unrelated_config_is_untouched(self, w, surface):
        w.register()
        assert not w.blocked(surface, SET8, texts=[PERM])
        assert not w.blocked(surface, "hermes config set display.skin default", texts=[PERM, TO9])

    def test_unrelated_later_chatter_does_not_supersede(self, w, surface):
        w.register()
        assert not w.blocked(surface, SET8, texts=[PERM, "Thanks, keep going.", "How is the rollout doing?"])


# ------------------------------------------------------------------------ 2. registration: non-decisions
class TestRegistrationRejectsNonDecisions:
    @pytest.mark.parametrize("text", [
        f"'I approve freezing root {KEY} at 7 (spec.md).'",
        f"\u2018I approve freezing root {KEY} at 7 (spec.md).\u2019",
        f"Did I approve freezing root {KEY} at 7 (spec.md)?",
        f"Do I approve freezing root {KEY} at 7 (spec.md)?",
        f"Should I approve freezing root {KEY} at 7 (spec.md)",
        f"Whether I approve freezing root {KEY} at 7 (spec.md) is undecided.",
        f"I approve freezing root {KEY} at 7 (spec.md)?",
        f"If I approve freezing root {KEY} at 7 (spec.md), what happens",
    ])
    def test_question_or_single_quoted_text_cannot_register(self, w, text):
        with pytest.raises(w.rc.RootCapError):
            w.register(approval_message_id=w.message(text))
        assert not (w.shared / "root_cap_contracts.json").exists()

    @pytest.mark.parametrize("text", [
        f"I approve freezing root {KEY} at 7 (spec.md).",
        f"I'm approving the freeze of root {KEY} at 7 (spec.md).",
        f"I approve freezing root {KEY} at 7 (the owner's spec.md).",
        f"Approved: freeze root {KEY} at 7 per spec.md.",
    ])
    def test_genuine_first_person_approval_still_registers(self, w, text):
        assert w.register(approval_message_id=w.message(text))["frozen_value"] == 7


# ------------------------------------------------------------------------ 3. ActionStore: native row + sourceRefs
def _refused(w, st, why, surface="terminal"):
    write_sources(w, st.source())
    out = w.call(surface, SET8, texts=[QUEUE])
    assert out and out["action"] == "block" and why in out["message"], out
    assert not w.blocked(surface, "hermes config set display.skin default")


class TestOperationNativeAuthority:
    @pytest.mark.parametrize("surface", SURFACES)
    @pytest.mark.parametrize("legacy", [False, True])
    def test_bound_and_legacy_with_native_row_and_owner_ref_protect(self, w, tmp_path, legacy, surface):
        st = Steps(w, tmp_path, bound=True)
        if legacy:  # an operation recorded before question bindings: accepted on the native/ref/intent equalities
            st.sql("update operations set body=? where id=?",
                   json.dumps({k: v for k, v in st.operation.items() if k != "binding"}), st.operation["operationId"])
        write_sources(w, st.source())
        assert w.blocked(surface, SET8, texts=[QUEUE])
        assert not w.blocked(surface, "hermes config set display.skin default")

    def test_genuine_later_native_steering_is_still_honoured(self, w, tmp_path):
        st = Steps(w, tmp_path, bound=True)
        write_sources(w, st.source())
        w.session("s-human")
        assert not w.blocked("terminal", SET8, session="s-human", texts=[PERM])

    @pytest.mark.parametrize("legacy", [False, True])
    def test_store_only_record_is_not_authority(self, w, tmp_path, legacy):
        st = Steps(w, tmp_path)  # bare: no native row, empty sourceRefs
        if legacy:
            st.sql("update operations set body=? where id=?",
                   json.dumps({k: v for k, v in st.operation.items() if k != "binding"}), st.operation["operationId"])
        source = st.source()
        with pytest.raises(w.rc.RootCapError):
            w.rc._verify_operation(source["authority"], source["mapping"], source["owner_card"], w.root)

    def test_missing_native_row_is_refused(self, w, tmp_path):
        st = Steps(w, tmp_path, bound=True)
        w.sdb.execute("delete from messages where id=?", (st.native_id,))
        w.sdb.commit()
        _refused(w, st, "does not exist")

    def test_assistant_authored_row_is_refused(self, w, tmp_path):
        st = Steps(w, tmp_path, bound=True)
        w.sdb.execute("update messages set role='assistant' where id=?", (st.native_id,))
        w.sdb.commit()
        _refused(w, st, "not a user row")

    def test_row_in_another_chat_is_refused(self, w, tmp_path):
        st = Steps(w, tmp_path, bound=True)
        w.sdb.execute("update messages set session_id='approval-chat' where id=?", (st.native_id,))
        w.sdb.commit()
        _refused(w, st, "another chat")

    def test_row_in_a_machine_chat_is_refused(self, w, tmp_path):
        st = Steps(w, tmp_path, bound=True)
        w.sdb.execute("update sessions set source='kanban' where id=?", (OWNER_SESSION,))
        w.sdb.commit()
        _refused(w, st, "not human authority")

    def test_row_that_is_not_a_canonical_decision_is_refused(self, w, tmp_path):
        st = Steps(w, tmp_path, bound=True)
        w.sdb.execute("update messages set content='I approve everything.' where id=?", (st.native_id,))
        w.sdb.commit()
        _refused(w, st, "not a canonical user decision")

    def test_row_embedding_a_different_request_is_refused(self, w, tmp_path):
        st = Steps(w, tmp_path, bound=True)
        text = st.native_text().replace("Worker-first", "Anything-goes")
        w.sdb.execute("update messages set content=? where id=?", (text, st.native_id))
        w.sdb.commit()
        _refused(w, st, "native request differs")

    def test_row_embedding_a_different_operation_is_refused(self, w, tmp_path):
        st = Steps(w, tmp_path, bound=True)
        text = st.native_text().replace(st.operation["operationId"], "00000000-0000-0000-0000-000000000000")
        w.sdb.execute("update messages set content=? where id=?", (text, st.native_id))
        w.sdb.commit()
        _refused(w, st, "native request differs")

    def test_descriptor_without_a_native_message_id_is_malformed_and_refuses(self, w, tmp_path):
        st = Steps(w, tmp_path, bound=True)
        src = st.source()
        del src["authority"]["native_message_id"]
        write_sources(w, src)
        out = w.call("terminal", SET8, texts=[QUEUE])
        assert out and out["action"] == "block" and "native_message_id" in out["message"], out

    @pytest.mark.parametrize("refs,why", [
        pytest.param([], "scope.sourceRefs", id="empty"),
        pytest.param([{"gatewayId": "test-gateway", "profile": "default", "type": "kanban", "id": "t_other",
                       "board": "default"}], "scope.sourceRefs", id="other-card"),
        pytest.param([{"gatewayId": "other", "profile": "default", "type": "kanban", "id": OWNER,
                       "board": "default"}], "scope.sourceRefs", id="other-gateway"),
        pytest.param([{"gatewayId": "test-gateway", "profile": "default", "type": "message", "id": OWNER}],
                     "scope.sourceRefs", id="not-a-card-ref"),
    ])
    def test_scope_source_refs_must_name_the_owner_card(self, w, tmp_path, refs, why):
        st = Steps(w, tmp_path, bound=True)
        # The stored revision AND the frozen intent both carry the other refs (a self-consistent but unbound request).
        for table, col in (("revisions", "body"),):
            row = sqlite3.connect(st.path).execute(f"select {col} from {table} where id=?",
                                                   (st.request["requestId"],)).fetchone()
            body = json.loads(row[0])
            body["scope"]["sourceRefs"] = refs
            st.sql(f"update {table} set {col}=? where id=?", json.dumps(body), st.request["requestId"])
        op = dict(st.operation)
        op["intent"] = {**op["intent"], "request": {**op["intent"]["request"],
                                                    "scope": {**op["intent"]["request"]["scope"], "sourceRefs": refs}}}
        st.sql("update operations set body=? where id=?", json.dumps(op), op["operationId"])
        _refused(w, st, why)


def test_declare_cli_operation_form_needs_and_takes_the_native_message(w, tmp_path, capsys):
    st = Steps(w, tmp_path, bound=True)
    common = ["declare", "--target-home", str(w.root), "--value", "7", "--contract-ref", str(w.artifact),
              "--owner-card", OWNER, "--action-db", str(st.path), "--operation", st.operation["operationId"],
              "--request", st.request["requestId"], "--revision", "1", "--gateway", "test-gateway",
              "--profile", "default", "--stored-session", OWNER_SESSION,
              "--mapping-phrase", "Hub cap7 across boards/profiles", "--mapping-rationale", "reviewed",
              "--mapping-reviewed-by", "tester"]
    assert w.rc.main(common) == 1  # no native row id: refused, nothing declared
    assert "native_message_id" in capsys.readouterr().out
    assert not (w.shared / "root_cap_sources.json").exists()
    assert w.rc.main([*common, "--native-message", str(st.native_id)]) == 0
    assert json.loads((w.shared / "root_cap_sources.json").read_text())[0]["authority"]["native_message_id"] == st.native_id

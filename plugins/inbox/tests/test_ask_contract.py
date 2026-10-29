"""The post gate is independent of transports and never trusts engagement as an ask."""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_missing_ask_is_refused_and_preserved_for_agents(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store = load("_ask_store", "store.py")
    cards = []
    box = store.Inbox(home=tmp_path, task_creator=lambda **kw: cards.append(kw) or "task-1")
    with pytest.raises(ValueError, match="ask_missing"):
        box.post("Investigate nightly job", "evidence", "Investigate", poster="cron · nightly", key="nightly:1")
    assert len(cards) == 1
    assert cards[0]["idempotency_key"] == "inbox-refused:cron:nightly:1"
    assert "evidence" in cards[0]["body"]
    assert not box.path.exists()  # no sidecar or session opened for a refusal


ASK = {"interaction": "approval", "why_human": "Changes the owner's publication policy", "action": "Approve publication"}


@pytest.mark.parametrize("poster,ask,reason", [
    (None, ASK, "poster_missing"),
    ("planted-unknown", ASK, "poster_unregistered"),
    ("cli", {}, "ask_missing"),
    ("cli", {**ASK, "interaction": "agent"}, "ask_invalid"),
    ("cli", {**ASK, "why_human": " "}, "ask_invalid"),
])
def test_contract_rejects_invalid_provenance_and_asks(tmp_path, poster, ask, reason):
    st = load("_ask_validation", "store.py")
    cards = []
    box = st.Inbox(home=tmp_path, task_creator=lambda **kw: cards.append(kw) or "task-2")
    with pytest.raises(ValueError, match=reason):
        box.post("Approve publication", "evidence", "Publish", poster=poster, ask=ask)
    assert len(cards) == 1


def test_pytest_cannot_write_real_inbox_even_with_injected_db(tmp_path, monkeypatch):
    st = load("_ask_isolation", "store.py")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "synthetic isolation check")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    cards = []
    box = st.Inbox(home=tmp_path / ".hermes", task_creator=lambda **kw: cards.append(kw))
    with pytest.raises(ValueError, match="test_isolation"):
        box.post("Approve publication", "evidence", "Publish", poster="cli", ask=ASK)
    assert cards == []  # test refusal itself must not create live agent work
    assert not box.path.exists()


def test_normalized_family_is_stable():
    policy = load("_ask_policy", "ask_contract.py")
    assert policy.family("Kanban · A different card") == "kanban"
    assert policy.family(None) == "unlabelled"


@pytest.mark.parametrize("title", ["Fixed: nightly job", "Hub change was rejected", "Mailbox expired", "X DM sent to a reader", "X posted: draft 7"])
def test_status_cannot_borrow_a_valid_ask(tmp_path, title):
    st = load("_ask_status", "store.py")
    cards = []
    box = st.Inbox(home=tmp_path, task_creator=lambda **kw: cards.append(kw) or "task-3")
    with pytest.raises(ValueError, match="content_status"):
        box.post(title, "evidence", "Review", poster="cli", ask=ASK)
    assert len(cards) == 1


@pytest.mark.parametrize("marker", ["TEST card", "live test", "SCRATCH TEST", "drill", "ignore"])
def test_test_markers_refused_even_on_decision_types(tmp_path, marker):
    policy = load("_ask_test_content", "ask_contract.py")
    assert policy.reason(home=tmp_path, poster="cli", ask=None, actions=[{"kind": "resolve"}],
                         title=marker) == "content_test"


def test_typed_decision_and_genuine_human_asks_allowed(tmp_path):
    policy = load("_ask_accept", "ask_contract.py")
    assert policy.reason(home=tmp_path, poster="cli", ask=None, actions=[{"kind": "resolve"}],
                         title="Approve draft", type_name="x-post") is None
    for interaction in policy.INTERACTIONS:
        assert policy.reason(home=tmp_path, poster="cli", ask={**ASK, "interaction": interaction},
                             actions=[], title="Choose publication policy") is None


def test_refusals_log_routing_failure_for_audit(tmp_path):
    st = load("_ask_routing_failure", "store.py")
    def broken(**kw):
        raise RuntimeError("board unavailable")
    box = st.Inbox(home=tmp_path, task_creator=broken)
    with pytest.raises(st.AgentOwnedPost, match="kanban routing failed"):
        box.post("Investigate nightly job", "evidence", "Investigate", poster="cli", key="job:7")
    events = [json.loads(s) for s in (tmp_path / "inbox" / "refusals.jsonl").read_text().splitlines()]
    assert events[-1]["reason"] == "ask_missing"
    assert events[-1]["key"] == "job:7"
    assert events[-1]["routing_error"] == "RuntimeError: board unavailable"


def test_legacy_agent_fixable_rule_also_routes_to_kanban(tmp_path):
    st = load("_ask_existing_rule", "store.py")
    cards = []
    box = st.Inbox(home=tmp_path, task_creator=lambda **kw: cards.append(kw) or "task-4")
    with pytest.raises(st.AgentFixablePost):
        box.post("Dispatch hold", "evidence", "Investigate", poster="kanban-dispatcher", key="dispatch:1")
    assert len(cards) == 1


def test_refusal_repeats_become_comments_on_the_same_kanban_card(tmp_path, monkeypatch):
    import sqlite3
    from agent import delegation_context
    original = delegation_context.kanban_path_is_fenced
    target = (tmp_path / "kanban.db").resolve()
    monkeypatch.setattr(delegation_context, "kanban_path_is_fenced",
                        lambda path: False if Path(path).resolve() == target else original(path))
    st = load("_ask_repeat_writer", "store.py")
    args = {"title": "Inbox refused: nightly", "body": "First evidence", "assignee": "default",
            "idempotency_key": "inbox-refused:cron:nightly"}
    first = st.kanban_task_creator(**args)
    second = st.kanban_task_creator(**{**args, "body": "Second evidence"})
    assert first == second
    with sqlite3.connect(tmp_path / "kanban.db") as db:
        assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        comments = db.execute("SELECT body FROM task_comments WHERE task_id=?", (first,)).fetchall()
        assert any("Second evidence" in body for (body,) in comments)


@pytest.mark.parametrize("title", ["X DM sent to Buck (@b), Thu Oct 22 11:40 PM PT", "X posted: Q-7", "X reply sent to @a"])
def test_sent_notices_are_refused_even_on_resolve_types(tmp_path, title):
    """Outgoing 'sent/posted' notices carry the x-dm/x-post resolve actions but are status, not a decision."""
    policy = load("_ask_sent", "ask_contract.py")
    assert policy.reason(home=tmp_path, poster="x-dm", ask=None, actions=[{"kind": "resolve"}],
                         title=title, type_name="x-dm") == "content_status"
    assert policy.reason(home=tmp_path, poster="x-dm", ask=None, actions=[{"kind": "resolve"}],
                         title="Reply to Buck?", type_name="x-dm") is None

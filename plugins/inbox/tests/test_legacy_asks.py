"""Follow-up to the zero-junk gate: the retry path for legacy asks, kanban human_request wiring, handle leaks."""
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
ASK = {"interaction": "approval", "why_human": "Needs the owner's account", "action": "Sign in to Bluesky"}


def load(name, filename="store.py"):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def box(tmp_path, st, cards=None):
    cards = [] if cards is None else cards
    return st.Inbox(home=tmp_path, task_creator=lambda **kw: cards.append(kw) or "task-1"), cards


# -- 1. the refusal names the retry shape --------------------------------------------------------

@pytest.mark.parametrize("ask", [None, {}, {"interaction": "agent", "why_human": "x", "action": "y"}])
def test_ask_refusal_names_exact_retry_shape(tmp_path, ask):
    st = load("_la_retry")
    b, cards = box(tmp_path, st)
    with pytest.raises(ValueError) as e:
        b.post("Sign in to Bluesky", "needs David", "Sign in", poster="kanban · bsky", key="bsky-signin:1", ask=ask)
    msg = str(e.value)
    assert "ask_" in msg
    assert "ask={" in msg and "interaction" in msg and "why_human" in msg and "action" in msg
    assert "approval|credential|publish_as_david|physical|preference" in msg
    assert "ask=" in cards[0]["body"]  # the Kanban copy carries the same instruction


def test_non_ask_refusals_do_not_get_the_retry_hint(tmp_path):
    st = load("_la_noretry")
    b, _ = box(tmp_path, st)
    with pytest.raises(ValueError) as e:
        b.post("Approve", "b", "go", poster="planted-unknown", ask=ASK)
    assert "ask={" not in str(e.value)


def test_retry_with_the_named_ask_is_accepted(tmp_path):
    st = load("_la_accept")
    b, _ = box(tmp_path, st)
    with pytest.raises(ValueError):
        b.post("Sign in to Bluesky", "needs David", "Sign in", poster="kanban · bsky", key="bsky-signin:1")
    assert b.post("Sign in to Bluesky", "needs David", "Sign in", poster="kanban · bsky", key="bsky-signin:1", ask=ASK)


# -- 2. kanban human_request -> ask, automatically -----------------------------------------------

def test_kanban_worker_post_inherits_ask_from_card_human_request(tmp_path, monkeypatch):
    st = load("_la_hr")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_card")
    monkeypatch.setattr(st, "card_human_request", lambda task_id: {
        "interaction": "credential", "why_human": "Only David holds the ElevenLabs key",
        "action": "Paste the ElevenLabs key into the vault"} if task_id == "t_card" else None)
    b, _ = box(tmp_path, st)
    out = b.post("ElevenLabs key", "needs a key", "Add the key", poster="kanban · eleven", key="eleven:1")
    assert out["session_id"]
    ask = json.loads(b._side().execute("select ask_json from items").fetchone()[0])
    assert ask["interaction"] == "credential"
    assert ask["why_human"].startswith("Only David")


@pytest.mark.parametrize("hr_interaction,want", [("user_terminal", "physical"), ("physical", "physical"),
                                                 ("reply", "preference"), ("external", "approval"),
                                                 ("approval", "approval")])
def test_human_request_interaction_maps_onto_inbox_interactions(hr_interaction, want):
    ac = load("_la_map", "ask_contract.py")
    got = ac.ask_from_human_request({"interaction": hr_interaction, "why_human": "w", "action": "a"})
    assert got == {"interaction": want, "why_human": "w", "action": "a"}


def test_incomplete_human_request_yields_no_ask():
    ac = load("_la_map2", "ask_contract.py")
    assert ac.ask_from_human_request({"interaction": "approval", "why_human": "", "action": "a"}) is None
    assert ac.ask_from_human_request(None) is None
    assert ac.ask_from_human_request("not json") is None


def test_explicit_ask_wins_over_card_human_request(tmp_path, monkeypatch):
    st = load("_la_explicit")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_card")
    monkeypatch.setattr(st, "card_human_request", lambda task_id: {
        "interaction": "credential", "why_human": "card", "action": "card"})
    b, _ = box(tmp_path, st)
    b.post("Approve spend", "b", "go", poster="kanban · x", key="bsky-signin:1", ask=ASK)
    assert json.loads(b._side().execute("select ask_json from items").fetchone()[0]) == ASK


def test_non_kanban_poster_never_inherits(tmp_path, monkeypatch):
    st = load("_la_nonkanban")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_card")
    monkeypatch.setattr(st, "card_human_request", lambda task_id: {
        "interaction": "credential", "why_human": "w", "action": "a"})
    b, _ = box(tmp_path, st)
    with pytest.raises(ValueError, match="ask_missing"):
        b.post("Investigate", "b", "go", poster="cron · nightly", key="n:1")


def test_card_without_human_request_still_refused(tmp_path, monkeypatch):
    st = load("_la_nohr")
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_card")
    monkeypatch.setattr(st, "card_human_request", lambda task_id: None)
    b, _ = box(tmp_path, st)
    with pytest.raises(ValueError, match="ask_missing"):
        b.post("Investigate", "b", "go", poster="kanban · x", key="n:2")


def test_card_human_request_reads_a_real_board(tmp_path, monkeypatch):
    st = load("_la_board")
    import sqlite3
    db = tmp_path / "kanban.db"
    c = sqlite3.connect(db)
    c.execute("create table tasks (id text primary key, human_request text)")
    c.execute("insert into tasks values ('t1', ?)", (json.dumps({"interaction": "approval", "why_human": "w", "action": "a"}),))
    c.execute("insert into tasks values ('t2', null)")
    c.commit()
    c.close()
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    assert st.card_human_request("t1")["action"] == "a"
    assert st.card_human_request("t2") is None
    assert st.card_human_request("missing") is None


def _wal_board_without_sidecars(tmp_path):
    import sqlite3
    db = tmp_path / "kanban.db"
    c = sqlite3.connect(db)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("create table tasks (id text primary key, human_request text)")
    c.execute("insert into tasks values ('t1', ?)", (json.dumps({"interaction": "credential", "why_human": "w", "action": "a"}),))
    c.commit()
    c.close()  # the last close checkpoints and deletes the sidecars, as Hermes writers do
    for suffix in ("-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    return db


def test_card_human_request_reads_a_wal_board_without_sidecars(tmp_path, monkeypatch):
    st = load("_la_wal")
    db = _wal_board_without_sidecars(tmp_path)
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    assert st.card_human_request("t1")["interaction"] == "credential"


def test_card_human_request_falls_back_when_a_plain_ro_open_fails(tmp_path, monkeypatch):
    # Apple's SQLite (the MacBook) refuses mode=ro on a WAL db whose -wal/-shm are gone; Linux's opens it.
    # Simulate that refusal so the fallback is proven on every host, not only where the bug bites.
    import sqlite3
    st = load("_la_wal_fallback")
    db = _wal_board_without_sidecars(tmp_path)
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db))
    real = sqlite3.connect

    def connect(target, *a, **kw):
        if "mode=ro" in str(target):
            raise sqlite3.OperationalError("unable to open database file")
        return real(target, *a, **kw)

    monkeypatch.setattr(sqlite3, "connect", connect)
    assert st.card_human_request("t1") == {"interaction": "credential", "why_human": "w", "action": "a"}
    monkeypatch.setattr(sqlite3, "connect", real)
    c = real(db)
    assert c.execute("select count(*) from tasks").fetchone()[0] == 1  # nothing written
    c.close()


def test_card_human_request_never_creates_a_missing_board(tmp_path, monkeypatch):
    st = load("_la_missing_board")
    missing = tmp_path / "nope" / "kanban.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(missing))
    assert st.card_human_request("t1") is None
    assert not missing.exists()
    missing.parent.mkdir()
    assert st.card_human_request("t1") is None
    assert not missing.exists()


# -- 3. test markers inside handles --------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "X reply R-1 to @scratchtest",
    "X reply R-1 to @scratch_acct",
    "X reply R-2 to @myscratch",
    "X reply R-3 to @test_account",
    "X reply R-4 to @test",
    "X reply R-5 to @acme-test",
    "X reply R-6 to @acme_test2",
    "Bluesky reply to @scratch.bsky.social",
])
def test_test_markers_inside_handles_are_junk(tmp_path, title):
    st = load("_la_handle_junk")
    b, _ = box(tmp_path, st)
    with pytest.raises(ValueError, match="test/probe"):
        b.post(title, "b", "go", poster="pr", key="x-reply:1", ask=ASK)


@pytest.mark.parametrize("title", [
    "X reply R-1 to @latest_news",
    "X reply R-2 to @contest",
    "X reply R-3 to @attestation",
    "X reply R-4 to @protester",
    "X reply R-5 to @greatest",
    "X reply R-6 to @testarossa",
    "Bluesky reply to @fastest.bsky.social",
])
def test_real_handles_with_test_inside_a_word_are_accepted(tmp_path, title):
    st = load("_la_handle_ok")
    b, _ = box(tmp_path, st)
    assert b.post(title, "b", "go", poster="pr", key="x-reply:1", ask=ASK)["session_id"]


def test_policy_layer_also_rejects_handle_markers():
    ac = load("_la_policy", "ask_contract.py")
    assert ac.reason(home="/nonexistent", poster="pr", ask=ASK, actions=[], title="X reply R-1 to @scratchtest") == "content_test"
    assert ac.reason(home="/nonexistent", poster="pr", ask=ASK, actions=[], title="X reply R-1 to @latest") is None

"""Junk guards (defect bb-ce4c46ed94): David's Archive sticks, every post has a sender, card-run posters are
counted per job, and test/probe posts never reach the real Inbox.

Before this, each fix blocked one sender by name, so any new sender got through: an archived alert came back
as a new card (`gh-actions-billing:2026-10`, archived 5 times in 4 days), posts with no poster were never
counted by the noise breaker, "kanban · <card title>" was a new poster per card, and TEST/probe cards landed
in the real Inbox.
"""
import argparse
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


store = _load("_inbox_store_junk", HERE / "store.py")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_state import SessionDB
    db = SessionDB(db_path=tmp_path / "state.db")
    db._inbox_keep_open = True
    clock = {"t": 1_000_000.0}
    tasks = []
    inbox = store.Inbox(home=tmp_path, db_factory=lambda: db, clock=lambda: clock["t"],
                        task_creator=lambda **kw: tasks.append(kw) or "t_junk")
    from ask_fixtures import register_test_posters, with_default_ask
    register_test_posters(tmp_path)
    with_default_ask(inbox)
    return inbox, db, clock, tasks


# ── 1. Archive sticks ───────────────────────────────────────────────────────────
def test_archived_key_is_refused_on_repost_and_files_one_kanban_card(env):
    inbox, _db, clock, tasks = env
    r = inbox.post("GitHub Actions: pick billing limit", "b", "fix", key="gh-actions-billing:2026-10", poster="cron · gh")
    inbox.archive(r["session_id"])
    clock["t"] += 3600
    with pytest.raises(store.ArchivedKey) as e:
        inbox.post("GitHub Actions: pick billing limit", "b", "fix", key="gh-actions-billing:2026-10", poster="cron · gh")
    assert "Kanban" in str(e.value) and "unarchive-key" in str(e.value)
    assert [t["idempotency_key"] for t in tasks] == ["inbox-archived-key:gh-actions-billing:2026-10"]


def test_sidebar_archive_also_sticks(env):
    inbox, db, clock, _ = env
    r = inbox.post("decide thing", "b", "fix", key="k:side", poster="p")
    db.set_session_archived(r["session_id"], True)  # archived through the normal session door
    clock["t"] += 60
    with pytest.raises(store.ArchivedKey):
        inbox.post("decide thing", "b", "fix", key="k:side", poster="p")


def test_archived_key_allowed_again_after_window_or_unarchive_key(env):
    inbox, _db, clock, _ = env
    inbox.archive(inbox.post("a", "b", "p", key="k:1", poster="p")["session_id"])
    clock["t"] += 60
    inbox.unarchive_key("k:1")
    clock["t"] += 60
    assert inbox.post("a", "b", "p", key="k:1", poster="p")["updated"] is False
    inbox.archive(inbox.post("c", "b", "p", key="k:2", poster="q")["session_id"])
    clock["t"] += store.ARCHIVE_STICK_S + 60
    assert inbox.post("c", "b", "p", key="k:2", poster="q")["updated"] is False


def test_resolved_or_accepted_key_is_not_refused(env):
    inbox, _db, clock, _ = env
    sid = inbox.post("a", "b", "p", key="k:acc", poster="p")["session_id"]
    inbox.accept(sid)
    clock["t"] += 60
    assert inbox.post("a2", "b", "p", key="k:acc", poster="p")["updated"] is True  # joins the conversation


# ── 2. Every post has a sender ──────────────────────────────────────────────────
def test_post_with_no_poster_and_no_key_is_refused(env):
    inbox, *_ = env
    with pytest.raises(store.AgentOwnedPost, match="poster_missing"):
        inbox.post("Approve spend", "b", "go")


def test_keyed_post_without_poster_is_refused_not_attributed(env):
    """v2: a key no longer stands in for a registered poster; the registry is fail-closed."""
    inbox, *_ = env
    with pytest.raises(store.AgentOwnedPost, match="poster_unregistered"):
        inbox.post("GitHub Actions: pick billing limit", "b", "fix", key="gh-actions-billing:2026-10")


def test_card_run_posters_are_counted_per_family_and_unmute_needs_a_fix_commit(env):
    inbox, _db, clock, tasks = env
    for name in ("A", "B"):
        clock["t"] += 60
        inbox.archive(inbox.post(f"noise {name}", "b", "p", key=f"n:{name}", poster=f"kanban · {name}")["session_id"])
    with pytest.raises(store.PosterMuted):
        inbox.post("noise D", "b", "p", key="n:D", poster="kanban · D")
    assert tasks[-1]["idempotency_key"] == "inbox-muted:kanban"
    with pytest.raises(ValueError, match="fix commit"):
        inbox.unmute("kanban · anything")
    inbox.unmute("kanban · anything", fix_commit="abc1234")  # unmute by any member names the family
    assert inbox.post("useful", "b", "p", key="n:E", poster="kanban · E")["updated"] is False


def test_cron_family_is_muted_after_two_archived_cards_v2(env):
    inbox, _db, clock, _ = env
    for name in ("A", "B"):
        clock["t"] += 60
        inbox.archive(inbox.post(f"x {name}", "b", "p", key=f"c:{name}", poster=f"cron · job {name}")["session_id"])
    with pytest.raises(store.PosterMuted):
        inbox.post("x D", "b", "p", key="c:D", poster="cron · job D")


# ── 3. Test/probe posts never reach the real Inbox ──────────────────────────────
@pytest.mark.parametrize("title,key", [
    ("TEST: x", None),
    ("TEST: approval binding, not for publication", "x-post:Q-TEST-APPROVAL-09593"),
    ("Bluesky reply draft: e2e routing probe", "bsky-reply:E2E-ROUTE-2027862"),
    ("X DM from SCRATCH TEST (not a real DM)", "x-dm:TEST-t01a17f04"),
    ("phase6 test A (safe to ignore)", "phase6-test-a"),
    ("Bluesky draft approval verification", "bsky-routing-probe:179304"),
    ("Merge PR #99 after CI", "coding:E2E-2027862"),
    ("LessWrong crosspost", "lw-crosspost:TEST-scratch"),
    ("cron wrapper", "cron:test-wrapper"),
    ("Inbox test from mini: archive me", None),
])
def test_test_and_probe_posts_are_refused(env, title, key):
    inbox, *_ = env
    with pytest.raises(store.JunkPost):
        inbox.post(title, "b", "p", key=key, poster="someone")


def test_dismissed_dm_family_is_muted_after_two_v2(env):
    """v2 mutes by family after two dismissals (the old per-key-only rule let x-dm noise through)."""
    inbox, _db, clock, _ = env
    for i in range(2):
        clock["t"] += 60
        inbox.archive(inbox.post(f"X DM from {i}", "b", "p", key=f"x-dm:{i}", poster="cli")["session_id"])
    with pytest.raises(store.PosterMuted):
        inbox.post("X DM from 5", "b", "p", key="x-dm:5", poster="cli")


@pytest.mark.parametrize("title,key", [
    ("organs: make livingroom-tests a required check on main?", "organs-require-livingroom-tests-check"),
    ("#quakes: Allie proposes moving past MeshRIR (D01 wave-speed test, then OpenFWI)", "quakes:next-benchmark"),
    ("Approve the latest contest entry", "release:latest"),
    ("GitHub Actions: billing lock or spend alert", "gh-actions-billing:2026-10"),
])
def test_real_cards_are_not_mistaken_for_tests(env, title, key):
    inbox, *_ = env
    assert inbox.post(title, "b", "p", key=key, poster="someone")["session_id"]


def test_test_env_refused_only_against_a_real_home(env, monkeypatch, tmp_path):
    inbox, *_ = env
    monkeypatch.setenv("HERMES_INBOX_TEST", "1")
    assert inbox.post("fine in a temp home", "b", "p", poster="x")["session_id"]  # tests use a temp home
    real = store.Inbox(home=Path.home() / ".hermes", db_factory=lambda: pytest.fail("must refuse first"))
    with pytest.raises(store.AgentOwnedPost, match="test_isolation"):
        real.post("ordinary title", "b", "p", poster="x")


# ── entry paths: CLI and tool ───────────────────────────────────────────────────
from ask_fixtures import ASK  # noqa: E402


def _plugin(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from ask_fixtures import register_test_posters
    register_test_posters(tmp_path)
    mod = _load("_inbox_plugin_junk", HERE / "__init__.py")
    monkeypatch.setattr(mod.store_module(), "kanban_task_creator", lambda **kw: "t_test")
    return mod


def _cli(mod, argv):
    p = argparse.ArgumentParser()
    mod._setup_cli(p)
    return mod._run_cli(p.parse_args(argv))


def test_cli_refuses_archived_key_and_unarchive_key_releases_it(monkeypatch, tmp_path, capsys):
    mod = _plugin(monkeypatch, tmp_path)
    assert _cli(mod, ["post", "decide thing", "--prompt", "p", "--key", "k:cli", "--poster", "cron · w", "--ask", json.dumps(ASK)]) == 0
    sid = json.loads(capsys.readouterr().out)["session_id"]
    assert _cli(mod, ["archive", sid]) == 0
    assert _cli(mod, ["post", "decide thing", "--prompt", "p", "--key", "k:cli", "--poster", "cron · w", "--ask", json.dumps(ASK)]) == 1
    assert "archived" in capsys.readouterr().err
    assert _cli(mod, ["unarchive-key", "k:cli"]) == 0
    assert _cli(mod, ["post", "decide thing", "--prompt", "p", "--key", "k:cli", "--poster", "cron · w", "--ask", json.dumps(ASK)]) == 0


def test_cli_refuses_post_with_no_poster_and_no_key(monkeypatch, tmp_path, capsys):
    mod = _plugin(monkeypatch, tmp_path)
    assert _cli(mod, ["post", "Approve spend", "--prompt", "p"]) == 1
    assert "poster" in capsys.readouterr().err


def test_tool_falls_back_to_a_poster_when_the_session_is_unknown(monkeypatch, tmp_path):
    mod = _plugin(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "_poster_for", lambda sid: (None, None))
    out = json.loads(mod.handle_post({"title": "Approve spend", "body": "b", "accept_prompt": "go", "poster": "cli", "ask": ASK}))
    assert out["posted"] is True
    item = mod.store_module().Inbox().get(out["session_id"])
    assert item["poster"] == "cli"


# ── detection: the daily cross-gateway audit opens one Kanban card, never an Inbox card ──────────────────
def test_junk_audit_finds_reposted_keys_untouched_senders_and_test_cards():
    audit = _load("_inbox_junk_audit", HERE / "junk_audit.py")
    now = 2_000_000.0
    item = lambda **kw: dict({"gw": "macbook", "state": "archived", "outcome": "archived", "poster": None}, **kw)  # noqa: E731
    items = [item(key="gh-actions-billing:2026-10", title="GitHub Actions: billing lock", created_at=now - 3 * 86400),
             item(key="gh-actions-billing:2026-10", title="GitHub Actions: billing lock", created_at=now - 3600),
             item(key="x-post:Q-TEST-1", title="TEST: approval binding", created_at=now - 7200, state="resolved"),
             item(key="ok:1", title="Approve spend", created_at=now - 3600, state="accepted", outcome="do_it",
                  poster="cron · audit"),
             item(key="old:1", title="old", created_at=now - 30 * 86400)]
    res = audit.audit(items, now)
    assert res["senders"] == []  # key-family senders are judged per key, below
    assert res["reposted_keys"][0]["key"] == "gh-actions-billing:2026-10" and res["reposted_keys"][0]["fresh"]
    assert [f["key"] for f in res["test_cards"]] == ["x-post:Q-TEST-1"]
    assert res["fresh"] is True
    calls = []

    class P:
        returncode, stdout, stderr = 0, '{"id": "t_audit"}', ""

    assert audit.file_card(res, now, runner=lambda argv, **kw: calls.append((argv, kw)) or P()) == "t_audit"
    argv, kw = calls[0]
    assert argv[:3] == ["hermes", "kanban", "create"] and "inbox" not in argv[1:3]
    assert argv[argv.index("--idempotency-key") + 1].startswith("inbox-junk-audit:")
    assert "gh-actions-billing" in kw["input"]
    assert audit.audit([i for i in items if i["key"] == "ok:1"], now)["fresh"] is False


def test_junk_audit_files_one_kanban_card_per_poster_family():
    audit = _load("_inbox_junk_audit_fam", HERE / "junk_audit.py")
    now = 2_000_000.0
    item = lambda **kw: dict({"gw": "hub", "state": "archived", "outcome": "archived", "poster": None}, **kw)  # noqa: E731
    items = [item(key="gh-actions-billing:a", title="billing lock", created_at=now - 3 * 86400),
             item(key="gh-actions-billing:a", title="billing lock", created_at=now - 3600),
             item(key="x-dm:TEST-1", title="TEST: dm", created_at=now - 3600, state="resolved"),
             item(key="x-dm:TEST-2", title="TEST: dm2", created_at=now - 3600, state="resolved")]
    res = audit.audit(items, now)
    calls = []

    class P:
        returncode, stdout, stderr = 0, '{"id": "t_fam"}', ""

    made = audit.file_cards(res, now, runner=lambda argv, **kw: calls.append((argv, kw)) or P())
    assert sorted(made) == ["gh-actions-billing", "x-dm"]
    keys = [a[a.index("--idempotency-key") + 1] for a, _ in calls]
    assert len(set(keys)) == 2 and all(k.startswith("inbox-junk-audit:") for k in keys)
    by_key = {a[a.index("--idempotency-key") + 1]: kw["input"] for a, kw in calls}
    only_dm = [v for k, v in by_key.items() if ":x-dm:" in k][0]
    assert "x-dm" in only_dm and "gh-actions-billing" not in only_dm


def test_audit_drains_refusals_whose_kanban_routing_was_fenced(tmp_path):
    """A refusal logged with routing_error (worker fence, board down) is filed as a Kanban card by the cron."""
    audit = _load("_inbox_junk_audit_drain", HERE / "junk_audit.py")
    log = tmp_path / "inbox" / "refusals.jsonl"
    log.parent.mkdir()
    lines = [{"at": 1.0, "family": "cron", "poster": "cron", "key": "k1", "title": "Hub change was rejected", "body": "b",
              "accept_prompt": "x", "reason": "content_status", "routing_error": "PermissionError: fenced"},
             {"at": 2.0, "family": "x-dm", "key": "k2", "title": "ok", "reason": "ask_missing", "task": "t_done"}]
    log.write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    made = []
    creator = lambda **kw: made.append(kw) or "t_new"  # noqa: E731
    assert audit.drain_refusals(tmp_path, creator) == 1
    assert made[0]["idempotency_key"] == "inbox-refused:cron:k1" and "content_status" in made[0]["body"]
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    assert rows[0]["task"] == "t_new" and "routing_error" not in rows[0] and rows[1]["task"] == "t_done"
    assert audit.drain_refusals(tmp_path, creator) == 0 and len(made) == 1  # idempotent

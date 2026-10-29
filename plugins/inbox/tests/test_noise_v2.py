"""Family-keyed noise containment and owner-triggered Junk reporting."""
import json
import pytest
from test_store import env, store


def test_two_archives_across_distinct_labels_mute_family(env):
    inbox, db, _ = env
    for i in range(2):
        inbox.archive(inbox.post("Choose wording", "evidence", "Choose", poster=f"kanban · card {i}", key=str(i))["session_id"])
    inbox._task_creator = lambda **kw: "noise-task"
    with pytest.raises(store.PosterMuted):
        inbox.post("Choose wording", "evidence", "Choose", poster="kanban · another card", key="third")


def test_unlabelled_historical_posts_count(env):
    inbox, db, clock = env
    for i in range(2):
        sid = inbox.post("Choose wording", "evidence", "Choose", key=str(i))["session_id"]
        inbox.archive(sid)
    with inbox._side() as side:
        side.execute("UPDATE items SET poster=NULL")
        assert inbox._ignored_streak(side, db, None, clock["t"]) == 2


def test_unmute_requires_recorded_fix_commit(env):
    inbox, _, _ = env
    with pytest.raises(ValueError, match="fix commit"):
        inbox.unmute("kanban")
    inbox.unmute("kanban · old title", fix_commit="a" * 40)
    with inbox._side() as side:
        row = side.execute("SELECT * FROM unmutes WHERE poster='kanban'").fetchone()
        assert row["fix_commit"] == "a" * 40


def test_junk_archives_mutes_prefix_and_files_five_whys(env):
    inbox, _, _ = env
    cards = []
    inbox._task_creator = lambda **kw: cards.append(kw) or "junk-task"
    sid = inbox.post("Choose wording", "evidence", "Choose", key="draft:1", poster="cron · editorial")["session_id"]
    result = inbox.junk(sid)
    assert result == {"ok": True, "task": "junk-task", "family": "cron", "key_prefix": "draft"}
    assert inbox.outcome("draft:1")["state"] == "archived"
    assert "five-whys" in cards[0]["body"] and sid in cards[0]["body"]
    with pytest.raises(store.PosterMuted):
        inbox.post("Choose wording", "evidence", "Choose", key="draft:2", poster="cron · new title")

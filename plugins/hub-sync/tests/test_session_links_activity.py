"""session_links carries each hub card's newest event (kanban-f241d1f60b).

The session panel calls a running card Stuck after 2 h with no event. Hub-replica rows carried no event time, so the
panel fell back to the card's first claim a week earlier and drafted "no progress since Fri 23 23:05" for a card
claimed 53 s before. The golden row below is also the input of session-panel's producer-composed test, which feeds
it through session-tasks' hubLinkChips and the panel's classify: a field dropped on either side fails a test.
"""
import json
import sqlite3
from pathlib import Path

from conftest import NOW

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "session_links_hub_card.json"
WEEK = 7 * 86400


def _insert_hub_card(state):
    data = dict(id="t_7e2ee45c", board="default", title="Busy hub card", status="running", assignee="default",
                created_at=NOW - WEEK - 60, started_at=NOW - WEEK,  # the first claim ever, a week ago
                run=dict(status="released", started_at=NOW - 3600, ended_at=NOW - 120),  # no running run this minute
                activity_at=NOW - 53, activity_kind="claimed",
                sessions=[dict(session="s_golden", gw="laptop", profile="default", rel="created")])
    with sqlite3.connect(state / "hub.db") as db:
        db.execute("INSERT INTO replica VALUES (?,?,?,?,?,?,?)",
                   ("cloud", "tasks", "default/default/t_7e2ee45c", 4, 1, json.dumps(data), int((NOW - 30) * 1000)))


def test_session_links_returns_the_newest_event(hub):
    hv, state = hub
    _insert_hub_card(state)
    [row] = hv.session_links(["s_golden"], origin_gateway="laptop", origin_profile="default")["tasks"]
    assert row["activity_at"] == NOW - 53
    assert row["activity_kind"] == "claimed"
    assert row["current_run_started_at"] is None
    golden = json.loads(GOLDEN.read_text())
    assert golden["now"] == NOW
    assert {k: row.get(k) for k in golden["row"]} == golden["row"], "regenerate the golden only with the new field set"


def test_a_row_without_events_reports_unknown_not_a_start_time(hub):
    hv, _ = hub
    mini = next(t for t in hv.session_links(["s_local"])["tasks"] if t["id"] == "t_mini")
    assert mini.get("activity_at") is None and mini.get("activity_kind") is None

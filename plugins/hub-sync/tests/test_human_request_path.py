"""Replica contract, including the real external producer (no network/live writes)."""
import json
import os
from pathlib import Path
import sqlite3

import pytest

from conftest import NOW, _load

EVIDENCE = dict(action="Choose a deployment window.", why_human="Owner scheduling decision.",
                interaction="reply", scope="The reviewed deployment.", evidence="No window selected.")


def test_session_links_preserves_evidence_and_owner(hub):
    hv, state = hub
    db = sqlite3.connect(state / "hub.db")
    row = db.execute("SELECT data FROM replica WHERE gw='mini' AND key='bb/t_mini'").fetchone()
    data = json.loads(row[0])
    data.update(block_kind="needs_input", human_request=EVIDENCE)
    db.execute("UPDATE replica SET data=? WHERE gw='mini' AND key='bb/t_mini'", (json.dumps(data),))
    db.commit()
    db.close()
    task = next(t for t in hv.session_links(["s_local"])["tasks"] if t["id"] == "t_mini")
    assert task["human_request"] == EVIDENCE
    assert task["block_kind"] == "needs_input"
    assert task["gateway"] == "mini"
    assert task["assignee"] == "worker"


# Use a sibling isolated checkout by default, never an installed/live producer.
# CI can point this explicitly at its hermes-hub source checkout.
PRODUCER = Path(os.environ.get("HERMES_HUB_CLIENT_REAL") or
                Path(__file__).resolve().parents[4] / "hub/client/hermes_hub.py")


@pytest.mark.parametrize("human_request", [EVIDENCE, {"action": "Choose a window."}])
def test_real_producer_serializes_human_request(tmp_path, human_request):
    path = PRODUCER
    assert path.is_file(), "Set HERMES_HUB_CLIENT_REAL to an isolated hermes-hub/client/hermes_hub.py"
    client = _load(path, "real_hub_evidence_producer")
    board = tmp_path / "kanban.db"
    db = sqlite3.connect(board)
    db.execute("CREATE TABLE tasks (id TEXT, title TEXT, status TEXT, created_at REAL, completed_at REAL, "
               "session_id TEXT, assignee TEXT, block_kind TEXT, human_request TEXT)")
    db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)", ("t_request", "Choose window", "blocked", NOW,
               None, "s_origin", "operator", "needs_input", json.dumps(human_request)))
    db.commit()
    db.close()
    collected = client.collect_tasks("ops", board, NOW)
    hubdb = client.open_db(tmp_path / "hub.db")
    try:
        assert client.stage(hubdb, {"tasks": {"ops/": collected}}) == 1
        published = json.loads(hubdb.execute("SELECT data FROM published WHERE key='ops/t_request'").fetchone()[0])
        outgoing = json.loads(hubdb.execute("SELECT data FROM outbox WHERE key='ops/t_request'").fetchone()[0])
        assert published == outgoing
        assert published["assignee"] == "operator"
        assert published["sessions"][0]["session"] == "s_origin"
        assert published["block_kind"] == "needs_input"
        assert published.get("human_request") == human_request, f"{path}: real serialized fields: {sorted(published)}"
    finally:
        hubdb.close()

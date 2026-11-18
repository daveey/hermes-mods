"""Prefetch is a final-row wire projection, never a different replica read."""
import copy
import json
import sqlite3
from contextlib import closing

import pytest

FIELDS = {"activity", "archived", "automated", "gateway", "hidden", "id", "message_count",
          "parent_session_id", "profile", "started_at"}


def projected(body):
    return dict(body, sessions=[{k: v for k, v in row.items() if k in FIELDS}
                               for row in body["sessions"]])


@pytest.mark.parametrize("query", ["", "?archived=1", "?gateway=mini", "?exclude=mini,cloud",
                                   "?limit=1", "?limit=2", "?limit=0", "?limit=9999"])
def test_endpoint_projection_preserves_selection_and_full_response(api_client, query):
    client, hv, _ = api_client
    full = client.get("/sessions" + query)
    assert full.status_code == 200
    cached = copy.deepcopy(hv._DECODED)
    response = client.get("/sessions" + query + ("&" if query else "?") + "projection=prefetch")
    assert response.status_code == 200
    assert response.json() == projected(full.json())
    assert hv._DECODED == cached
    assert client.get("/sessions" + query).content == full.content


@pytest.mark.parametrize("value", ["full", "unknown", "", "PREFETCH"])
def test_unknown_projection_is_422(api_client, value):
    client, _, _ = api_client
    assert client.get("/sessions", params={"projection": value}).status_code == 422


def test_synthetic_fixture_endpoint_parity_and_body_bytes(api_client):
    """Shared Desktop fixture: synthetic existing row fields, NOT production measurements."""
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / "fixtures" /
                          "hub-sync-sessions-prefetch-projection.json").read_text())
    assert set(fixture["prefetch_fields"]) == FIELDS
    client, hv, state = api_client
    rows = fixture["full"]["sessions"]
    with closing(sqlite3.connect(state / "hub.db")) as db, db:
        db.execute("DELETE FROM published WHERE coll='sessions'")
        db.execute("DELETE FROM replica WHERE coll='sessions'")
        for i, row in enumerate(rows):
            db.execute("INSERT INTO replica VALUES (?,?,?,?,?,?,?)",
                       (row["gateway"], "sessions", str(i), 1, i + 1, json.dumps(row), 1700000000000))
    full = client.get("/sessions?archived=1")
    small = client.get("/sessions?archived=1&projection=prefetch")
    expected = sorted(rows, key=lambda r: -(r.get("activity") or r.get("started_at") or 0))
    assert small.json()["sessions"] == projected({"sessions": expected})["sessions"]
    assert small.json() == projected(full.json())
    assert len(small.content) <= len(full.content) * 0.5
    print(f"synthetic endpoint bytes: full={len(full.content)} prefetch={len(small.content)} "
          f"reduction={1 - len(small.content) / len(full.content):.2%}")
    # Keep the JSON's omitted fields omitted and explicit null values null.
    by_id = {r["id"]: r for r in small.json()["sessions"]}
    assert "profile" not in by_id["missing-profile"]
    assert by_id["null-profile"]["profile"] is None
    assert "message_count" not in by_id["missing-count"]
    assert by_id["null-count"]["message_count"] is None
    assert "activity" not in by_id["missing-activity"]
    assert by_id["null-activity"]["activity"] is None
    assert by_id["null-parent"]["parent_session_id"] is None
    assert client.get("/sessions?archived=1").content == full.content


@pytest.mark.parametrize("state_kind", ["unavailable", "empty", "offline"])
def test_projection_preserves_envelope(api_client, monkeypatch, state_kind):
    client, hv, state = api_client
    if state_kind == "unavailable":
        monkeypatch.setattr(hv, "load_client", lambda: None)
    else:
        with closing(sqlite3.connect(state / "hub.db")) as db, db:
            if state_kind == "empty":
                db.execute("DELETE FROM published WHERE coll='sessions'")
                db.execute("DELETE FROM replica WHERE coll='sessions'")
            else:
                db.execute("UPDATE meta SET v='1' WHERE k='last_ok'")
                db.execute("INSERT INTO meta VALUES ('offline_since', '2')")
    full = client.get("/sessions").json()
    assert client.get("/sessions?projection=prefetch").json() == projected(full)
    if state_kind == "offline":
        assert full["stale"] is True and full["offline_since"] == 2
    else:
        assert full["sessions"] == []
        assert full["available"] is (state_kind == "empty")


@pytest.mark.parametrize("mode", ["primary", "shadow", "off"])
def test_shared_overrides_pending_and_cache_nonmutation_without_real_client(api_client, mode):
    client, hv, state = api_client
    with closing(sqlite3.connect(state / "hub.db")) as db, db:
        db.execute("CREATE TABLE overrides (gw TEXT, coll TEXT, key TEXT, field TEXT, value TEXT, "
                   "hlc TEXT, by_gw TEXT, seq INTEGER)")
        db.execute("INSERT INTO meta VALUES ('shared_fields', ?)", (json.dumps(mode),))
        for key, field, value in [("default/s_mini", "archived", True),
                                  ("bb/s_old", "archived", False),
                                  ("bb/s_old", "hidden", True),
                                  ("bb/s_old", "title", "Shared rename"),
                                  ("bb/s_old", "pending_action", {"request_id": "pending"})]:
            db.execute("INSERT INTO overrides VALUES (?,?,?,?,?,?,?,?)",
                       ("mini", "sessions", key, field, json.dumps(value), "1-0-cloud", "cloud", 50))
    full = client.get("/sessions?archived=1").json()
    cached = copy.deepcopy(hv._DECODED)
    all_rows = {r["id"]: r for r in full["sessions"]}
    assert all_rows["s_mini"]["pending"]
    if mode == "primary":
        assert all_rows["s_old"]["hidden"] is True
        assert all_rows["s_old"]["pending_action"] == {"request_id": "pending"}
        assert all_rows["s_old"]["shared"]["title"]["owner_value"] == "Archived"
    for query in ("", "?archived=1", "?gateway=mini", "?exclude=cloud&limit=2", "?limit=1"):
        full = client.get("/sessions" + query).json()
        small = client.get("/sessions" + query + ("&" if query else "?") + "projection=prefetch").json()
        assert small == projected(full)
    selected = client.get("/sessions?gateway=mini&projection=prefetch").json()["sessions"]
    assert [r["id"] for r in selected] == (["s_old"] if mode == "primary" else ["s_mini"])
    assert hv._DECODED == cached

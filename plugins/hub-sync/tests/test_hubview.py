"""hub-sync views and requests over a fixture hub.db.

Run: uv run --with pytest --with fastapi --with httpx python -m pytest -q -p no:cacheprovider plugins/hub-sync/tests
"""
import json
import sqlite3

import pytest

from conftest import NOW, build_db


def _by(rows, field="key"):
    return {(r["gateway"], r[field]): r for r in rows}


# ---------------------------------------------------------------- reads

def test_sessions_merge_own_from_published_and_others_from_replica(hub):
    hv, _ = hub
    res = hv.sessions()
    assert res["available"] is True and res["gateway"] == "laptop"
    rows = res["sessions"]
    keys = [(r["gateway"], r["key"]) for r in rows]
    assert keys.count(("laptop", "default/s_local")) == 1  # own echo in replica is ignored
    own = _by(rows)[("laptop", "default/s_local")]
    assert own["local"] is True and own["stale"] is False and own["version"] == 3  # published version wins
    mini = _by(rows)[("mini", "default/s_mini")]
    assert mini["local"] is False and mini["stale"] is False
    # synced_at = the owner's newest record we pulled (NOW-300 is mini's newest), capped by our last pull
    assert mini["synced_at"] == pytest.approx(NOW - 300)
    assert mini["pending"] == [{"id": "hr-old", "action": "pin", "args": {}, "state": "queued",
                                "created_at": NOW - 20}]
    assert ("mini", "bb/s_old") not in _by(rows)  # archived hidden by default
    assert [r["key"] for r in rows][:2] == ["default/s_local", "alpha/s_cloud"]  # newest activity first


def test_sessions_filters_archived_gateway_and_exclude(hub):
    hv, _ = hub
    assert ("mini", "bb/s_old") in _by(hv.sessions(include_archived=True)["sessions"])
    assert {r["gateway"] for r in hv.sessions(gateway="mini")["sessions"]} == {"mini"}
    assert {r["gateway"] for r in hv.sessions(exclude=["mini", "laptop"])["sessions"]} == {"cloud"}


def test_replica_rows_are_stale_when_the_last_pull_is_old(tmp_path, monkeypatch, fake_client):
    state = tmp_path / "s"
    state.mkdir()
    build_db(state / "hub.db", fake_client.SCHEMA, last_ok=NOW - 3600, offline_since=NOW - 3500)
    monkeypatch.setenv("HERMES_HUB_STATE", str(state))
    from conftest import PLUGIN, _load
    hv = _load(PLUGIN / "hubview.py", "hv_stale")
    monkeypatch.setattr(hv, "load_client", lambda: fake_client)
    monkeypatch.setattr(hv, "_now", lambda: NOW)
    res = hv.sessions()
    assert res["stale"] is True and res["offline_since"] == NOW - 3500
    rows = _by(res["sessions"])
    assert rows[("mini", "default/s_mini")]["stale"] is True
    assert rows[("mini", "default/s_mini")]["synced_at"] == NOW - 3600  # capped by our last successful pull
    assert rows[("laptop", "default/s_local")]["stale"] is False  # own rows are live


def test_inbox_lists_open_cards_of_every_gateway(hub):
    hv, _ = hub
    items = _by(hv.inbox()["items"], "session_id")
    assert set(items) == {("laptop", "s_card"), ("mini", "s_mcard")}
    assert items[("mini", "s_mcard")]["version"] == 7 and items[("mini", "s_mcard")]["actions"][0]["id"] == "approve"
    assert ("mini", "s_done") in _by(hv.inbox(include_closed=True)["items"], "session_id")


def test_tasks_view(hub):
    hv, _ = hub
    rows = hv.tasks()["tasks"]
    assert [(r["gateway"], r["id"]) for r in rows] == [("mini", "t_mini"), ("laptop", "t_own")]  # priority desc
    assert [r["id"] for r in hv.tasks(board="bb", include_closed=True)["tasks"]] == ["t_mini", "t_done"]


def test_where_finds_the_owner(hub):
    hv, _ = hub
    hits = hv.where("s_mini")["hits"]
    assert hits == [{"gateway": "mini", "profile": "default", "session": "s_mini",
                     "ref": "@session:default@mini/s_mini", "title": "Mini chat", "local": False, "stale": False,
                     "synced_at": NOW - 300, "archived": False}]
    assert hv.where("s_local")["hits"][0]["local"] is True
    assert hv.where("nope")["hits"] == []


def test_session_links_cover_lineage_and_other_gateways(hub):
    hv, _ = hub
    tasks = hv.session_links(["s_local"])["tasks"]
    assert [(t["gateway"], t["id"], t["via"]) for t in tasks] == [("mini", "t_mini", "subscribed"),
                                                                  ("laptop", "t_own", "created")]
    mini = tasks[0]
    assert mini["board"] == "bb" and mini["status"] == "unknown" and mini["current_run_started_at"] == NOW - 60
    assert mini["profile"] is None and mini["reported_status"] == "blocked"
    # a lineage id that created the card wins over the subscription
    assert hv.session_links(["s_local", "s_parent"])["tasks"][0]["via"] == "created"
    assert "t_done" in {t["id"] for t in hv.session_links(["s_local"], include_closed=True)["tasks"]}


def test_status_lists_gateways(hub):
    hv, _ = hub
    st = hv.status()
    gws = {g["gateway"]: g for g in st["gateways"]}
    assert set(gws) == {"laptop", "mini", "cloud"}
    assert gws["laptop"]["local"] and gws["mini"]["boards"] == ["default", "bb"]
    assert gws["cloud"]["seen_at"] == pytest.approx(NOW - 100)
    assert st["requests_pending"] == 1 and st["cursor"] == 42


def test_reads_never_write_hub_db(hub):
    hv, state = hub
    before = (state / "hub.db").read_bytes()
    hv.sessions(), hv.inbox(), hv.tasks(), hv.where("s_mini"), hv.session_links(["s_local"]), hv.status()
    assert (state / "hub.db").read_bytes() == before


# ---------------------------------------------------------------- unavailable

def test_without_the_client_everything_is_empty_and_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HUB_CLIENT", str(tmp_path / "missing.py"))
    monkeypatch.setenv("HERMES_HUB_STATE", str(tmp_path / "nostate"))
    from conftest import PLUGIN, _load
    hv = _load(PLUGIN / "hubview.py", "hv_missing")
    assert hv.sessions() == dict(hv.sessions(), available=False, sessions=[])
    assert hv.inbox()["items"] == [] and hv.tasks()["tasks"] == [] and hv.where("x")["hits"] == []
    assert hv.session_links(["x"])["tasks"] == [] and hv.status()["available"] is False
    assert not (tmp_path / "nostate").exists()  # never creates the state dir
    with pytest.raises(hv.RequestError) as e:
        hv.request({"gateway": "mini", "coll": "sessions", "key": "default/s", "action": "pin"})
    assert e.value.status == 503


def test_client_installed_but_no_db_yet(tmp_path, monkeypatch, fake_client):
    monkeypatch.setenv("HERMES_HUB_STATE", str(tmp_path / "empty"))
    from conftest import PLUGIN, _load
    hv = _load(PLUGIN / "hubview.py", "hv_nodb")
    monkeypatch.setattr(hv, "load_client", lambda: fake_client)
    assert hv.sessions()["available"] is False
    assert not (tmp_path / "empty" / "hub.db").exists()


def test_links_qualified_origin_and_owner_collisions(api_client):
    c, hv, state = api_client
    with sqlite3.connect(state / "hub.db") as db:
        for gateway, profile, origin_gateway, origin_profile in [
            ("mini", "work", "laptop", "work"),
            ("mini", "default", "laptop", "default"),
            ("cloud", "work", "other", "work"),
        ]:
            data = dict(id="t_same", board="bb", status="running", sessions=[
                dict(session="s_same", gw=origin_gateway, profile=origin_profile, rel="created")])
            db.execute("INSERT INTO replica VALUES (?,?,?,?,?,?,?)", (
                gateway, "tasks", f"{profile}/bb/t_same", 1, 1, json.dumps(data), int(NOW * 1000)))
        data = dict(id="t_legacy", board="bb", status="running", sessions=[dict(session="s_same")])
        db.execute("INSERT INTO replica VALUES (?,?,?,?,?,?,?)", (
            "mini", "tasks", "bb/t_legacy", 1, 1, json.dumps(data), int(NOW * 1000)))
    tasks = c.get('/session-links/s_tip?ids=s_same&origin_profile=work').json()['tasks']
    known = [t for t in tasks if t['id'] == 't_same']
    assert [(t['gateway'], t['profile'], t['board']) for t in known] == [('mini', 'work', 'bb')]
    legacy = next(t for t in tasks if t['id'] == 't_legacy')
    assert legacy['profile'] is None and legacy['status'] == 'unknown'
    assert legacy['ownership_state'] == 'unknown'
    other = c.get('/session-links/s_same?origin_gateway=other&origin_profile=work').json()['tasks']
    assert [(t['gateway'], t['profile']) for t in other if t['id'] == 't_same'] == [('cloud', 'work')]


def test_links_binding_origin_alias_and_missing_owner_provenance(hub):
    hv, state = hub
    with sqlite3.connect(state / "hub.db") as db:
        for key in ["work/bb/t_bound", "bb/t_historical"]:
            tid = key.split('/')[-1]
            data = dict(id=tid, board="bb", status="running", origin_bindings=[
                dict(origin=dict(gateway="laptop", profile="work", session="ancestor"))])
            db.execute("INSERT INTO replica VALUES (?,?,?,?,?,?,?)", (
                "mini", "tasks", key, 1, 1, json.dumps(data), int(NOW * 1000)))
    rows = hv.session_links(["tip", "ancestor"], origin_profile="work")["tasks"]
    by_id = {t['id']: t for t in rows}
    assert by_id['t_bound']['profile'] == 'work'
    assert by_id['t_bound']['status'] == 'running'
    assert by_id['t_historical']['profile'] is None
    assert by_id['t_historical']['status'] == 'unknown'
    assert hv.session_links(["ancestor"], origin_profile="default")["tasks"] == []
    assert hv.session_links(["ancestor"], origin_gateway="cloud", origin_profile="work")["tasks"] == []


# ---------------------------------------------------------------- requests

def _sent(state):
    db = sqlite3.connect(str(state / "hub.db"))
    try:
        return {r[0]: (json.loads(r[1]), r[2]) for r in db.execute("SELECT id, body, state FROM sent")}
    finally:
        db.close()


def test_request_offline_queues_locally(hub, fake_client):
    hv, state = hub
    res = hv.request({"gateway": "mini", "coll": "sessions", "key": "default/s_mini", "action": "rename",
                      "args": {"title": "New name"}})
    assert res["queued_locally"] is True and res["state"] == "local" and res["id"].startswith("hr-")
    body, st = _sent(state)[res["id"]]
    assert st == "local" and body == {"id": res["id"], "to_gw": "mini", "coll": "sessions", "key": "default/s_mini",
                                      "action": "rename", "args": {"title": "New name"}}
    # it shows as pending on the row right away
    row = _by(hv.sessions()["sessions"])[("mini", "default/s_mini")]
    assert [p["action"] for p in row["pending"]] == ["pin", "rename"]


def test_request_online_is_sent(hub, fake_client):
    hv, state = hub
    fake_client.HUB_UP = True
    res = hv.request({"gateway": "mini", "coll": "inbox", "key": "default/s_mcard", "action": "act",
                      "args": {"action": "approve"}, "base_version": 7})
    assert res["queued_locally"] is False and res["state"] == "queued"
    assert _sent(state)[res["id"]][1] == "queued"
    assert fake_client.POSTED[-1][2]["base_version"] == 7


@pytest.mark.parametrize("body,status,needle", [
    ({"gateway": "laptop", "coll": "sessions", "key": "default/s_local", "action": "pin"}, 400, "this gateway"),
    ({"gateway": "nowhere", "coll": "sessions", "key": "default/s", "action": "pin"}, 404, "unknown gateway"),
    ({"gateway": "mini", "coll": "sessions", "key": "default/s_mini", "action": "delete"}, 400, "supports"),
    ({"gateway": "mini", "coll": "sessions", "key": "default/s_mini", "action": "rename"}, 400, "needs title"),
    ({"gateway": "mini", "coll": "sessions", "key": "s_mini", "action": "pin"}, 400, "key must be"),
    ({"gateway": "mini", "coll": "sessions", "key": "../x", "action": "pin"}, 400, "key must be"),
    ({"gateway": "mini", "coll": "mail", "key": "a/b", "action": "pin"}, 400, "coll must be"),
    ({"gateway": "mini", "coll": "tasks", "key": "bb/t_mini", "action": "create", "args": {"title": "x"}}, 400,
     "<board>/new"),
    ({"gateway": "mini", "coll": "tasks", "key": "bb/t_mini", "action": "comment", "args": {"text": "x", "evil": 1}},
     400, "unexpected args"),
    ({"gateway": "mini", "coll": "inbox", "key": "default/s_mcard", "action": "archive", "base_version": "v"}, 400,
     "integer"),
])
def test_request_validation(hub, body, status, needle):
    hv, state = hub
    before = set(_sent(state))
    with pytest.raises(hv.RequestError) as e:
        hv.request(body)
    assert e.value.status == status and needle in e.value.detail
    assert set(_sent(state)) == before  # nothing queued


def test_tasks_create_on_another_gateway(hub):
    hv, state = hub
    res = hv.request({"gateway": "mini", "coll": "tasks", "key": "bb/new", "action": "create",
                      "args": {"title": "Do it there", "body": "b"}})
    assert _sent(state)[res["id"]][0]["args"] == {"title": "Do it there", "body": "b"}


def test_inbox_act_keeps_input_verbatim(hub):
    hv, state = hub
    res = hv.request({"gateway": "mini", "coll": "inbox", "key": "default/s_mcard", "action": "act",
                      "args": {"action": "edit", "input": "  spaced  "}, "base_version": 7})
    assert _sent(state)[res["id"]][0]["args"] == {"action": "edit", "input": "  spaced  "}


# ---------------------------------------------------------------- REST

def test_rest_routes(api_client):
    c, hv, state = api_client
    assert c.get("/ping").json()["ok"] is True
    assert c.get("/status").json()["gateway"] == "laptop"
    assert {r["gateway"] for r in c.get("/sessions?exclude=laptop,cloud").json()["sessions"]} == {"mini"}
    assert ("mini", "bb/s_old") in _by(c.get("/sessions?archived=1").json()["sessions"])
    assert len(c.get("/inbox").json()["items"]) == 2
    assert len(c.get("/inbox?all=1").json()["items"]) == 3
    assert [t["id"] for t in c.get("/tasks?board=bb").json()["tasks"]] == ["t_mini"]
    assert c.get("/where/s_cloud").json()["hits"][0]["gateway"] == "cloud"
    links = c.get("/session-links/s_parent?ids=s_x").json()
    assert links["session_ids"] == ["s_parent", "s_x"] and [t["id"] for t in links["tasks"]] == ["t_mini"]
    assert {r["id"] for r in c.get("/requests").json()["requests"]} == {"hr-old", "hr-done"}


def test_rest_post_request(api_client):
    c, hv, state = api_client
    r = c.post("/request", json={"gateway": "mini", "coll": "sessions", "key": "default/s_mini", "action": "archive"})
    assert r.status_code == 200 and r.json()["queued_locally"] is True
    r = c.post("/request", json={"gateway": "mini", "coll": "sessions", "key": "default/s_mini", "action": "nuke"})
    assert r.status_code == 400 and "supports" in r.json()["detail"]
    r = c.post("/request", json={"gateway": "zzz", "coll": "sessions", "key": "default/s", "action": "pin"})
    assert r.status_code == 404

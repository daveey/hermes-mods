"""Phase 6b: project groups from the replica (hermes-hub client >= 1.9.0).

``hubview.projects`` draws each gateway's tree from its published project nodes and its sessions' ``project`` stamp,
with a shared name/icon/color on top in primary mode; a rename of another gateway's project is a set, never a
request to the owner.
"""
import json
import os
import sqlite3
from pathlib import Path

import pytest

from conftest import NOW, PLUGIN, _load, build_db

CANDIDATES = [os.environ.get("HERMES_HUB_CLIENT_REAL"), str(Path.home() / "code" / "hermes-hub" / "client" / "hermes_hub.py")]
CLIENT = next((Path(p) for p in CANDIDATES if p and Path(p).is_file() and "def project_record" in Path(p).read_text()), None)
pytestmark = pytest.mark.skipif(CLIENT is None, reason="no hermes-hub client with projects (>= 1.9.0)")


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("HERMES_HUB_STATE", str(state))
    monkeypatch.setenv("HERMES_HUB_CLIENT", str(CLIENT))
    monkeypatch.setenv("HERMES_MAIL_CONFIG", str(tmp_path / "cfg" / "config.json"))
    monkeypatch.setenv("HERMES_HUB_URL", "http://127.0.0.1:9")
    client = _load(CLIENT, "projects_hermes_hub")
    path = state / "hub.db"
    build_db(path, client.SCHEMA)
    c = sqlite3.connect(str(path))

    def rep(coll, key, data, gw="mini"):
        c.execute("INSERT OR REPLACE INTO replica VALUES (?,?,?,?,?,?,?)",
                  (gw, coll, key, 1, 1, json.dumps(data), int((NOW - 60) * 1000)))

    node = lambda **kw: dict({"kind": "explicit", "profile": "default", "icon": "", "color": "", "isAuto": False,  # noqa: E731
                              "isNoProject": False, "sessionCount": 2, "lastActive": NOW - 5,
                              "repos": [{"id": "/r/gd", "label": "gd", "path": "/r/gd", "sessionCount": 2,
                                         "groups": [{"id": "main", "label": "main", "isMain": True}]}]}, **kw)
    rep("projects", "default/p_gd", node(id="p_gd", name="Garden", label="Garden", path="/r/gd",
                                         previewIds=["s_a", "s_b"]))
    rep("projects", "bb/auto:1", node(kind="auto", profile="bb", id="/r/gd", label="gd", path="/r/gd", isAuto=True,
                                     sessionCount=1, previewIds=["s_c"]))
    rep("projects", "default/none", node(kind="none", id="__no_project__", label="Home", path=None, isNoProject=True,
                                        sessionCount=1, repos=[], previewIds=["s_h"]))
    for sid, prof, proj, title in (("s_a", "default", "p_gd", "first"), ("s_b", "default", "p_gd", "second"),
                                   ("s_c", "bb", "auto:1", "bb chat"), ("s_h", "default", "none", "home chat")):
        rep("sessions", "%s/%s" % (prof, sid), {"profile": prof, "id": sid, "title": title, "project": proj,
                                                 "activity": NOW - (1 if sid == "s_b" else 10)})
    c.commit()
    c.close()
    hv = _load(PLUGIN / "hubview.py", "hv_projects")
    monkeypatch.setattr(hv, "_now", lambda: NOW)
    return hv, client, path


def _mode(db_path, mode):
    c = sqlite3.connect(str(db_path))
    c.execute("INSERT INTO meta (k, v) VALUES ('shared_fields', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (json.dumps(mode),))
    c.commit()
    c.close()


def _override(db_path, key, field, value, coll="projects"):
    c = sqlite3.connect(str(db_path))
    c.execute("INSERT OR REPLACE INTO overrides (gw, coll, key, field, value, hlc, by_gw, seq) VALUES (?,?,?,?,?,?,?,?)",
              ("mini", coll, key, field, json.dumps(value), "1700000000000-0000-cloud", "cloud", 50))
    c.commit()
    c.close()


def _tree(hv):
    return hv.projects(gateway="mini")["gateways"]["mini"]["projects"]


def test_tree_shape_previews_ids_and_profile_merge(env):
    hv, client, db = env
    out = hv.projects(gateway="mini")
    assert out["available"] is True
    tree = out["gateways"]["mini"]["projects"]
    pb = next(p for p in tree if p["path"] == "/r/gd")
    # bb's auto group for the same folder folds into the declared project, which keeps its label.
    assert pb["label"] == "Garden" and pb["isAuto"] is False and pb["sessionCount"] == 3
    assert {(m["profile"], m["kind"]) for m in pb["members"]} == {("default", "explicit"), ("bb", "auto")}
    assert sorted(pb["sessionIds"]) == ["s_a", "s_b", "s_c"]
    assert [s["title"] for s in pb["previewSessions"]] == ["second", "first", "bb chat"]
    assert all(g["sessions"] == [] for r in pb["repos"] for g in r["groups"])
    home = next(p for p in tree if p["isNoProject"])
    assert home["sessionIds"] == ["s_h"]
    assert sorted(out["gateways"]["mini"]["scoped_session_ids"]) == ["s_a", "s_b", "s_c", "s_h"]


def test_shared_name_icon_color_win_in_primary_only(env):
    hv, client, db = env
    _override(db, "default/p_gd", "name", "Garden RL")
    _override(db, "default/p_gd", "color", "#ff8800")
    _mode(db, "off")
    assert next(p for p in _tree(hv) if p["path"] == "/r/gd")["label"] == "Garden"
    _mode(db, {"projects": "primary"})
    pb = next(p for p in _tree(hv) if p["path"] == "/r/gd")
    assert pb["label"] == "Garden RL" and pb["color"] == "#ff8800"
    assert pb["shared"]["name"] == {"by": "cloud", "owner_value": "Garden"}


def test_marker_goes_once_the_owner_has_the_value_and_empty_icon_is_none(env):
    hv, client, db = env
    _mode(db, "primary")
    _override(db, "default/p_gd", "name", "Garden")
    _override(db, "default/p_gd", "icon", "")
    pb = next(p for p in _tree(hv) if p["path"] == "/r/gd")
    assert pb["label"] == "Garden" and pb["icon"] is None and "shared" not in pb


def test_previews_show_a_shared_session_title(env):
    hv, client, db = env
    _mode(db, "primary")
    _override(db, "default/s_b", "title", "renamed on cloud", coll="sessions")
    pb = next(p for p in _tree(hv) if p["path"] == "/r/gd")
    assert pb["previewSessions"][0]["title"] == "renamed on cloud"


def test_rename_in_primary_is_a_set(env):
    hv, client, db = env
    _mode(db, "primary")
    res = hv.request({"gateway": "mini", "coll": "projects", "key": "default/p_gd", "action": "rename",
                      "args": {"name": "From the laptop"}}, wait_s=5)
    assert res["state"] == "applied" and res["via"] == "shared_field"
    res2 = hv.request({"gateway": "mini", "coll": "projects", "key": "default/p_gd", "action": "icon",
                       "args": {"icon": ""}}, wait_s=5)
    assert res2["via"] == "shared_field"
    c = client.open_db(db)
    try:
        out = {(r["field"], r["value"]) for r in c.execute("SELECT field, value FROM shared_out")}
        assert out == {("name", '"From the laptop"'), ("icon", '""')}
        states = {r["state"] for r in c.execute("SELECT state FROM sent WHERE id IN (?, ?)", (res["id"], res2["id"]))}
        assert states == {"done"}, "never sent to the owner as a request"
    finally:
        c.close()
    assert next(p for p in _tree(hv) if p["path"] == "/r/gd")["label"] == "From the laptop"


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_rename_outside_primary_is_refused(env, mode):
    hv, client, db = env
    _mode(db, mode)
    with pytest.raises(hv.RequestError) as e:
        hv.request({"gateway": "mini", "coll": "projects", "key": "default/p_gd", "action": "rename",
                    "args": {"name": "x"}}, wait_s=5)
    assert e.value.status == 409

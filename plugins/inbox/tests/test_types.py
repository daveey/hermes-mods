"""Card types: sources, validation, each action kind, outcomes (store, tool, CLI, REST).

Run: see ../README.md (Tests).
"""
import argparse
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


store = _load("_inbox_store_types_test", HERE / "store.py")
ct = store.types_module()

XPOST = """
name: x-post
label: X post
fields:
  id:   {required: true}
  text: {required: true, preview: true, max: 280}
  slot: {}
actions:
  - {id: approve, label: Approve, icon: check, style: primary, kind: resolve, row: true}
  - {id: edit,    label: Edit,    icon: edit,  kind: resolve, input: {field: text, multiline: true, max: 280}}
  - {id: skip,    label: Skip,    icon: close, kind: resolve}
  - id: rewrite
    label: Rewrite…
    kind: prompt
    input: {placeholder: What to change}
    prompt: "Rewrite X draft {{id}}: {{input}}. Update QUEUE.md and re-post key x-post:{{id}}."
"""


@pytest.fixture
def skills(tmp_path):
    d = tmp_path / "skills" / "social-media" / "x-presence" / "inbox-types"
    d.mkdir(parents=True)
    (d / "x-post.yaml").write_text(XPOST)
    return tmp_path / "skills"


@pytest.fixture
def env(tmp_path, monkeypatch, skills):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_state import SessionDB
    db = SessionDB(db_path=tmp_path / "state.db")
    db._inbox_keep_open = True
    clock = {"t": 1_000_000.0}
    reg = ct.Registry(tmp_path, skill_dirs=[skills])
    from ask_fixtures import decision_inbox
    inbox = decision_inbox(store.Inbox(home=tmp_path, db_factory=lambda: db, clock=lambda: clock["t"], types=reg))
    yield inbox, db, clock
    db.close()


def _post_x(inbox, id_="Q1", text="Cells are agents.", key=None):
    return inbox.post(f"X post pending: {id_}", "", type="x-post", key=key or f"x-post:{id_}",
                      fields={"id": id_, "text": text, "slot": "Tue 9am"}, poster="cron · x-daily")


# ── sources ────────────────────────────────────────────────────────────────
def test_three_sources_and_local_wins(tmp_path, skills):
    ext = tmp_path / "ext"
    (ext / "inbox" / "inbox-types").mkdir(parents=True)
    (ext / "inbox" / "inbox-types" / "ext-only.yaml").write_text(
        "actions: [{id: ok, kind: resolve}]\n")
    reg = ct.Registry(tmp_path, skill_dirs=[skills, ext])
    assert reg.get("x-post")["source"] == "skill:x-presence"
    assert reg.get("ext-only")["source"] == "skill:inbox"
    assert reg.get("default")["source"] == "builtin"
    assert reg.get(None)["actions"][0]["id"] == "do_it"
    with pytest.raises(ValueError, match="owned by skill:x-presence"):
        reg.define("x-post", {"actions": [{"id": "only", "kind": "resolve"}]})  # define won't shadow
    (tmp_path / "inbox" / "types").mkdir(parents=True)
    (tmp_path / "inbox" / "types" / "x-post.yaml").write_text("actions: [{id: only, kind: resolve}]\n")
    got = reg.get("x-post")
    assert got["source"] == "local" and [a["id"] for a in got["actions"]] == ["only"]
    assert {t["name"]: t["source"] for t in reg.list()} == {
        "default": "builtin", "ext-only": "skill:inbox", "x-post": "local"}
    with pytest.raises(ValueError, match="unknown inbox card type"):
        reg.get("nope")


def test_default_skill_dirs_include_external_dirs(tmp_path, monkeypatch):
    """Without agent.skill_utils the registry falls back to <home>/skills + config external_dirs."""
    import builtins
    real = builtins.__import__

    def no_agent(name, *a, **k):
        if name.startswith("agent"):
            raise ImportError(name)
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_agent)
    ext = tmp_path / "mods-skills"
    (ext / "cat" / "s" / "inbox-types").mkdir(parents=True)
    (ext / "cat" / "s" / "inbox-types" / "t1.yaml").write_text("actions: [{id: a, kind: resolve}]\n")
    (tmp_path / "config.yaml").write_text(f"skills:\n  external_dirs:\n    - {ext}\n")
    assert ct.Registry(tmp_path).get("t1")["source"] == "skill:s"


def test_parse_falls_back_to_ruamel_then_json(monkeypatch):
    import builtins
    real = builtins.__import__

    def block(names):
        def imp(name, *a, **k):
            if name in names:
                raise ImportError(name)
            return real(name, *a, **k)
        return imp
    monkeypatch.setattr(builtins, "__import__", block({"yaml"}))
    assert ct.parse("a: {b: 1}") == {"a": {"b": 1}}  # ruamel
    monkeypatch.setattr(builtins, "__import__", block({"yaml", "ruamel.yaml"}))
    assert ct.parse('{"a": 1}') == {"a": 1}
    with pytest.raises(ValueError, match="no YAML parser"):
        ct.parse("a: 1")


def test_yaml_is_safe_loaded():
    with pytest.raises(Exception):
        ct.parse("!!python/object/apply:os.system ['echo pwned']")


# ── validation ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("spec,msg", [
    ({"actions": [{"id": "a", "kind": "resolve"}, {"id": "a", "kind": "resolve"}]}, "duplicate action id"),
    ({"actions": [{"id": "a", "kind": "bogus"}]}, "unknown kind"),
    ({"actions": [{"id": "a", "kind": "prompt"}]}, "needs a prompt"),
    ({"actions": [{"id": "archive", "kind": "resolve"}]}, "reserved"),
    ({"actions": []}, "non-empty"),
    ({"fields": {"x": {}}, "actions": [{"id": "a", "kind": "prompt", "prompt": "{{y}}"}]}, "undeclared"),
    ({"fields": {"x": {}}, "actions": [{"id": "a", "kind": "resolve", "input": {"field": "y"}}]}, "not a declared"),
    ({"actions": [{"id": "a", "kind": "resolve", "icon": "x\" onclick=1"}]}, "codicon"),
    ({"actions": [{"id": "a", "kind": "resolve"}], "evil": 1}, "unknown type keys"),
    ({"actions": [{"id": "a", "kind": "prompt", "prompt": "p", "row": True}]}, "row buttons"),
    ({"actions": [{"id": "a", "kind": "resolve", "input": {"field": "key"}}]}, "not a declared"),
    ({"description": ["x"], "actions": [{"id": "a", "kind": "resolve"}]}, "description must be a string"),
    ("actions: [{id: a, kind: resolve", "bad YAML"),
])
def test_bad_types_rejected(tmp_path, spec, msg):
    with pytest.raises(ValueError, match=msg):
        ct.Registry(tmp_path, skill_dirs=[]).define("t", spec)
    assert not (tmp_path / "inbox" / "types" / "t.yaml").exists()


@pytest.mark.parametrize("name", ["../x", "X", "a/b", "", ".hidden", "a" * 65, "a.yaml", "foo\n"])
def test_type_names_cannot_escape(tmp_path, name):
    reg = ct.Registry(tmp_path, skill_dirs=[])
    with pytest.raises(ValueError, match="type name"):
        reg.define(name, {"actions": [{"id": "a", "kind": "resolve"}]})
    if name:  # get("") = the default type
        with pytest.raises(ValueError):
            reg.get(name)


def test_post_checks_required_and_max(env):
    inbox, _, _ = env
    with pytest.raises(ValueError, match="requires field 'text'"):
        inbox.post("t", "", type="x-post", fields={"id": "Q1"})
    with pytest.raises(ValueError, match="over its max of 280"):
        inbox.post("t", "", type="x-post", fields={"id": "Q1", "text": "x" * 281})
    with pytest.raises(ValueError, match="has no field 'nope'"):
        inbox.post("t", "", type="x-post", fields={"id": "Q1", "text": "x", "nope": "1"})
    with pytest.raises(ValueError, match="unknown inbox card type"):
        inbox.post("t", "", type="missing")
    with pytest.raises(ValueError, match="accept_prompt is required"):
        inbox.post("t", "b", "")  # the default type still needs its prompt


def test_default_cannot_be_redefined(tmp_path):
    with pytest.raises(ValueError, match="built in"):
        ct.Registry(tmp_path, skill_dirs=[]).define("default", {"actions": [{"id": "a", "kind": "resolve"}]})


@pytest.mark.parametrize("parser", ["pyyaml", "ruamel"])
def test_yaml_alias_bomb_rejected_fast(parser, monkeypatch):
    import builtins
    import time
    if parser == "ruamel":
        real = builtins.__import__
        monkeypatch.setattr(builtins, "__import__",
                            lambda n, *a, **k: (_ for _ in ()).throw(ImportError(n)) if n == "yaml" else real(n, *a, **k))
    bomb = "a: &a [x, x, x, x, x, x, x, x, x]\n" + "".join(
        f"{c}: &{c} [*{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}, *{p}]\n" for p, c in zip("abcdefgh", "bcdefghi"))
    bomb += "name: t\ndescription: *i\nactions: [{id: a, kind: resolve}]\n"
    t0 = time.time()
    with pytest.raises(ValueError, match="aliases"):
        ct.validate(ct.parse(bomb))
    assert time.time() - t0 < 1


def test_yaml_12_booleans_ids_like_no_stay_strings():
    spec = ct.validate(ct.parse("name: yn\nfields: {on: {}}\nactions: [{id: no, label: No, kind: resolve}, "
                                "{id: yes, kind: resolve, row: true}]\n"))
    assert [a["id"] for a in spec["actions"]] == ["no", "yes"] and spec["actions"][0]["label"] == "No"
    assert list(spec["fields"]) == ["on"] and spec["actions"][1]["row"] is True


def test_concurrent_act_only_one_wins(env):
    """Another process already moved the card on: this act() must not overwrite its outcome."""
    inbox, db, _ = env
    sid = _post_x(inbox)["session_id"]
    stale = inbox.get(sid)
    inbox.act(sid, "skip")
    inbox.get = lambda _sid: dict(stale)  # this caller read the card while it was still open
    assert inbox.act(sid, "approve") is None
    assert inbox.accept(sid) is None
    del inbox.get
    assert inbox.outcome("x-post:Q1")["outcome"] == "skip"
    assert db.get_session(sid)["hidden"]  # the loser did not un-hide it


def test_render_is_one_plain_pass():
    out = ct.render("A {{id}} B {{input}} {{ missing }}", {"id": "{{input}}", "input": "x"})
    assert out == "A {{input}} B x "


# ── posting / snapshot ─────────────────────────────────────────────────────
def test_typed_post_snapshots_type_and_shows_preview(env):
    inbox, db, _ = env
    sid = _post_x(inbox)["session_id"]
    [item] = inbox.list()
    pub = store.public_item(item)
    assert pub["type"] == "x-post" and pub["type_label"] == "X post"
    assert pub["fields"] == {"id": "Q1", "text": "Cells are agents.", "slot": "Tue 9am"}
    assert [a["id"] for a in pub["actions"]] == ["approve", "edit", "skip", "rewrite"]
    assert pub["actions"][1]["input"] == {"multiline": True, "field": "text", "max": 280, "required": True}
    text = db.get_messages(sid)[0]["content"]
    assert "> Cells are agents." in text and "17/280 chars" in text
    assert "**Approve**, **Edit**, **Skip**, **Rewrite…**, **Archive**" in text
    assert "Press **Do it**" not in text


def test_repost_refreshes_snapshot_and_fields(env, skills):
    inbox, db, clock = env
    a = inbox.post("legacy card", "old", "post Q1", key="x-post:Q1")  # posted before types existed
    clock["t"] += 5
    (skills / "social-media" / "x-presence" / "inbox-types" / "x-post.yaml").write_text(
        XPOST.replace("label: Approve", "label: Ship it"))
    b = _post_x(inbox, text="new text")
    assert b == {"session_id": a["session_id"], "updated": True}
    pub = store.public_item(inbox.list()[0])
    assert pub["type"] == "x-post" and pub["fields"]["text"] == "new text"
    assert pub["actions"][0]["label"] == "Ship it"
    assert "> new text" in db.get_messages(a["session_id"])[-1]["content"]


def test_snapshot_survives_type_edits(env, skills):
    inbox, _, _ = env
    _post_x(inbox)
    (skills / "social-media" / "x-presence" / "inbox-types" / "x-post.yaml").unlink()
    pub = store.public_item(inbox.list()[0])
    assert [a["id"] for a in pub["actions"]] == ["approve", "edit", "skip", "rewrite"]


# ── actions ────────────────────────────────────────────────────────────────
def test_resolve_records_outcome_and_closes(env):
    inbox, db, clock = env
    sid = _post_x(inbox)["session_id"]
    clock["t"] += 3
    item = inbox.act(sid, "approve")
    assert item["state"] == "resolved" and item["outcome"] == "approve" and item["outcome_value"] is None
    assert db.get_session(sid)["archived"] and inbox.list() == []
    out = inbox.outcome("x-post:Q1")
    assert out["state"] == "resolved" and out["outcome"] == "approve" and out["at"] == clock["t"]
    assert inbox.act(sid, "skip") is None  # closed cards take no more actions


def test_resolve_with_input_writes_back_the_field(env):
    inbox, _, _ = env
    sid = _post_x(inbox)["session_id"]
    with pytest.raises(ValueError, match="needs an input"):
        inbox.act(sid, "edit", "  ")
    with pytest.raises(ValueError, match="over the max of 280"):
        inbox.act(sid, "edit", "y" * 281)
    inbox.act(sid, "edit", "Cells are agents, and so are you.")
    out = inbox.outcome("x-post:Q1")
    assert out["outcome"] == "edit" and out["value"] == "Cells are agents, and so are you."
    assert out["fields"]["text"] == "Cells are agents, and so are you."


def test_prompt_action_renders_and_accepts(env):
    inbox, db, _ = env
    sid = _post_x(inbox)["session_id"]
    item = inbox.act(sid, "rewrite", "shorter, {{id}} stays literal")
    assert item["kind"] == "prompt" and item["state"] == "accepted"
    assert item["prompt"] == ("Rewrite X draft Q1: shorter, {{id}} stays literal. Update QUEUE.md and "
                              "re-post key x-post:Q1.")
    assert not db.get_session(sid)["hidden"] and not db.get_session(sid)["archived"]
    assert inbox.outcome("x-post:Q1")["state"] == "accepted"
    # a decision card re-posted after a rewrite is a NEW card with buttons, not a chat note
    again = _post_x(inbox, text="shorter")
    assert again["session_id"] != sid and not again["updated"]


def test_unknown_action_rejected(env):
    inbox, _, _ = env
    sid = _post_x(inbox)["session_id"]
    with pytest.raises(ValueError, match="no action 'post'"):
        inbox.act(sid, "post")


def test_archive_records_archived_outcome(env):
    inbox, db, _ = env
    sid = _post_x(inbox)["session_id"]
    assert inbox.outcome("x-post:Q1")["state"] == "open"
    inbox.archive(sid)
    out = inbox.outcome("x-post:Q1")
    assert out["state"] == "archived" and out["outcome"] == "archived"
    assert inbox.outcome("x-post:none") is None


def test_archive_via_act_and_sidebar_archive_both_read_as_archived(env):
    inbox, db, _ = env
    a = _post_x(inbox, "Q1")["session_id"]
    inbox.act(a, "archive")
    assert inbox.outcome("x-post:Q1")["outcome"] == "archived"
    b = _post_x(inbox, "Q2")["session_id"]
    db.set_session_archived(b, True)  # archived through core, not the inbox
    assert inbox.outcome("x-post:Q2")["state"] == "archived"


def test_archiving_a_resolved_card_keeps_its_outcome(env):
    inbox, _, _ = env
    sid = _post_x(inbox)["session_id"]
    inbox.act(sid, "skip")
    inbox.archive(sid)
    assert inbox.outcome("x-post:Q1")["outcome"] == "skip"
    assert inbox.outcome("x-post:Q1")["state"] == "resolved"


def test_legacy_row_without_type_is_the_default_type(env, tmp_path):
    inbox, db, _ = env
    sid = inbox.post("old", "b", "do the old thing", key="k")["session_id"]
    con = sqlite3.connect(tmp_path / "inbox" / "inbox.db")
    con.execute("UPDATE items SET type=NULL, fields_json=NULL, actions_json=NULL")
    con.commit()
    con.close()
    pub = store.public_item(inbox.list()[0])
    assert pub["type"] == "default" and [a["id"] for a in pub["actions"]] == ["do_it"]
    item = inbox.act(sid, None)
    assert item["prompt"] == "do the old thing" and item["accept_prompt"] == "do the old thing"
    assert inbox.outcome("k")["outcome"] == "do_it"


def test_migration_adds_columns_to_an_old_sidecar(tmp_path):
    (tmp_path / "inbox").mkdir()
    con = sqlite3.connect(tmp_path / "inbox" / "inbox.db")
    con.executescript(store._SCHEMA)
    con.execute("INSERT INTO items(session_id,key,title,poster,accept_prompt,created_at,updated_at) "
                "VALUES ('s','k','t',NULL,'p',1,1)")
    con.commit()
    con.close()
    inbox = store.Inbox(home=tmp_path)
    for _ in range(2):  # idempotent
        cols = {r[1] for r in inbox._side().execute("PRAGMA table_info(items)")}
    assert {"type", "fields_json", "actions_json", "outcome", "outcome_value", "resolved_at"} <= cols
    assert inbox.get("s")["type"] is None


# ── tool / CLI ─────────────────────────────────────────────────────────────
def _plugin(inbox, monkeypatch, name):
    plugin = _load(name, HERE / "__init__.py")
    monkeypatch.setattr(plugin, "store_module", lambda: type("M", (), {"Inbox": lambda *a, **k: inbox, "TaskError": store.TaskError}))
    monkeypatch.setattr(plugin, "_poster_for", lambda sid: ("cron · x", "/w"))
    return plugin


def test_tools_post_typed_and_read_outcome(env, monkeypatch):
    inbox, _, _ = env
    plugin = _plugin(inbox, monkeypatch, "_inbox_plugin_types1")
    r = json.loads(plugin.handle_post({"title": "X post pending: Q5", "body": "", "type": "x-post", "key": "x-post:Q5",
                                       "fields": {"id": "Q5", "text": "hi"}}, session_id="s"))
    assert r["posted"], r
    assert json.loads(plugin.handle_outcome({"key": "x-post:Q5"}))["state"] == "open"
    inbox.act(r["session_id"], "skip")
    out = json.loads(plugin.handle_outcome({"key": "x-post:Q5"}))
    assert out["found"] and out["outcome"] == "skip"
    assert json.loads(plugin.handle_outcome({"key": "nope"})) == {"found": False, "key": "nope"}
    bad = json.loads(plugin.handle_post({"title": "t", "body": "", "type": "x-post", "fields": {"id": "Q"}}))
    assert not bad["posted"] and "requires field" in bad["error"]
    types = json.loads(plugin.handle_types({"action": "list"}))["types"]
    assert {t["name"] for t in types} == {"default", "x-post"}
    d = json.loads(plugin.handle_types({"action": "define", "name": "yn",
                                        "spec": "actions: [{id: ok, kind: resolve}, {id: nah, kind: resolve}]"}))
    assert d["defined"] and d["source"] == "local"
    assert json.loads(plugin.handle_types({"action": "get", "name": "yn"}))["name"] == "yn"
    assert "type name" in json.loads(plugin.handle_types({"action": "define", "name": "../x", "spec": {}}))["error"]
    assert plugin.SCHEMA["parameters"]["required"] == ["title", "body"]


def test_cli_post_act_outcome_types(env, monkeypatch, capsys):
    inbox, _, _ = env
    plugin = _plugin(inbox, monkeypatch, "_inbox_plugin_types2")
    ap = argparse.ArgumentParser()
    plugin._setup_cli(ap)

    def run(*argv):
        rc = plugin._run_cli(ap.parse_args(list(argv)))
        return rc, capsys.readouterr()
    rc, out = run("post", "X post pending: Q7", "--type", "x-post", "--key", "x-post:Q7",
                  "--field", "id=Q7", "--field", "text=a=b")
    assert rc == 0, out.err
    sid = json.loads(out.out)["session_id"]
    assert store.public_item(inbox.get(sid))["fields"] == {"id": "Q7", "text": "a=b"}
    rc, out = run("act", sid, "edit", "--input", "edited")
    assert rc == 0 and json.loads(out.out)["outcome"] == "edit"
    rc, out = run("outcome", "x-post:Q7")
    assert rc == 0 and json.loads(out.out)["value"] == "edited"
    rc, out = run("outcome", "x-post:missing")
    assert rc == 1 and json.loads(out.out)["found"] is False
    rc, out = run("types")
    assert rc == 0 and "x-post" in out.out and "approve, edit, skip, rewrite" in out.out
    rc, out = run("types", "x-post")
    assert rc == 0 and json.loads(out.out)["source"] == "skill:x-presence"
    rc, out = run("post", "t", "--type", "x-post", "--field", "id=Q8")
    assert rc == 1 and "requires field" in out.err


# ── task kind ──────────────────────────────────────────────────────────────
TASKY = {
    "label": "Chore",
    "fields": {"id": {"required": True}, "text": {"max": 50}},
    "actions": [
        {"id": "skip", "label": "Skip", "kind": "task", "assignee": "helper", "skills": ["s-one"],
         "prompt": "Mark {{id}} skipped ({{key}}): {{input}}", "input": {"field": "text", "required": False}},
        {"id": "plain", "label": "Plain", "kind": "task", "row": True, "prompt": "Do {{id}}",
         "title": "Chore {{id}}"},
    ],
}


class FakeKanban:
    def __init__(self, fail=None):
        self.created, self.fail = [], fail

    def __call__(self, **kw):
        if self.fail:
            raise self.fail
        self.created.append(kw)
        return f"t_fake{len(self.created)}"


@pytest.fixture
def tenv(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_state import SessionDB
    db = SessionDB(db_path=tmp_path / "state.db")
    db._inbox_keep_open = True
    reg = ct.Registry(tmp_path, skill_dirs=[])
    reg.define("chore", TASKY)
    kb = FakeKanban()
    inbox = store.Inbox(home=tmp_path, db_factory=lambda: db, types=reg, task_creator=kb)
    yield inbox, db, kb
    db.close()


def _post_chore(inbox, id_="C1"):
    return inbox.post(f"Chore {id_}", "", type="chore", key=f"chore:{id_}", fields={"id": id_, "text": "x"})["session_id"]


def test_task_act_creates_one_card_records_outcome_and_closes(tenv):
    inbox, db, kb = tenv
    sid = _post_chore(inbox)
    item = inbox.act(sid, "skip", "because")
    assert len(kb.created) == 1
    k = kb.created[0]
    assert k["body"] == "Mark C1 skipped (chore:C1): because" and k["assignee"] == "helper"
    assert k["skills"] == ["s-one"] and k["board"] is None
    assert k["title"] == "Chore: Skip \u2014 Chore C1"  # default "<type label>: <action label> — <card title>"
    assert k["idempotency_key"] == f"inbox:{sid}:skip"
    assert item["state"] == "resolved" and item["outcome_task"] == "t_fake1" and item["kind"] == "task"
    assert db.get_session(sid)["archived"] and inbox.list() == []
    out = inbox.outcome("chore:C1")
    assert out["state"] == "resolved" and out["outcome"] == "skip" and out["value"] == "because"
    assert out["task"] == "t_fake1" and out["fields"]["text"] == "because"
    assert inbox.act(sid, "plain") is None and len(kb.created) == 1  # closed: no second card


def test_task_defaults_and_title_template(tenv):
    inbox, _, kb = tenv
    sid = _post_chore(inbox, "C2")
    inbox.act(sid, "plain")
    k = kb.created[0]
    assert k["assignee"] == "default" and k["title"] == "Chore C2" and k["skills"] is None


def test_task_failure_leaves_the_card_open(tenv, tmp_path):
    inbox, db, kb = tenv
    sid = _post_chore(inbox, "C3")
    kb.fail = RuntimeError("board locked")
    with pytest.raises(store.TaskError, match="board locked"):
        inbox.act(sid, "skip", "why")
    [item] = inbox.list()
    assert item["session_id"] == sid and item["state"] == "open" and item["outcome"] is None
    assert item["outcome_task"] is None and store.item_fields(item)["text"] == "x"  # edit not kept
    assert not db.get_session(sid)["archived"] and inbox.outcome("chore:C3")["state"] == "open"
    kb.fail = None
    assert inbox.act(sid, "skip", "why")["outcome_task"] == "t_fake1"  # retry works


def test_task_validation_rejects(tmp_path):
    reg = ct.Registry(tmp_path, skill_dirs=[])
    a = {"id": "a", "kind": "task", "prompt": "p"}
    for bad, msg in [
        ({"id": "a", "kind": "task"}, "needs a prompt"),
        (dict(a, assignee="bad name"), "assignee must match"),
        (dict(a, assignee="x\n"), "assignee must match"),
        (dict(a, board="../x"), "board must match"),
        (dict(a, skills=["ok", "no/slash"]), "skills must be"),
        (dict(a, skills="one"), "skills must be"),
        (dict(a, title="  "), "title must be"),
        (dict(a, prompt="{{nope}}"), "undeclared"),
        (dict(a, title="{{nope}}"), "undeclared"),
        (dict(a, board="a.b"), "no dots"),
        (dict(a, assignee="a.b"), "no dots"),
        ({"id": "a", "kind": "resolve", "assignee": "x"}, "only for kind task"),
        (dict(a, input={"field": "text"}, row=True), "row buttons"),
    ]:
        with pytest.raises(ValueError, match=msg):
            reg.define("t", {"fields": {"text": {}}, "actions": [bad]})
    ok = reg.define("t", {"actions": [dict(a, assignee="Helper_1-x", board="main", skills=["s", "s"])]})
    act = ok["actions"][0]
    assert act["assignee"] == "Helper_1-x" and act["board"] == "main" and act["skills"] == ["s"]


def test_task_rendered_body_is_capped(tenv):
    inbox, _, kb = tenv
    inbox.types.define("big", {"fields": {"id": {}, "text": {}},
                               "actions": [{"id": "go", "kind": "task", "prompt": "{{text}}{{text}}"}]})
    sid = inbox.post("b", "", type="big", fields={"id": "1", "text": "x" * ct.MAX_FIELD_VALUE})["session_id"]
    with pytest.raises(ValueError, match="over"):
        inbox.act(sid, "go")
    assert kb.created == [] and [i["state"] for i in inbox.list()] == ["open"]


def test_task_claim_is_invisible_until_the_card_exists_and_survives_a_failed_record(tenv):
    inbox, db, kb = tenv
    sid = _post_chore(inbox, "C8")
    seen = {}

    def creator(**kw):
        seen["mid"] = inbox.outcome("chore:C8")  # while queuing: still no decision, no task
        seen["listed"] = [i["session_id"] for i in inbox.list()]
        return kb(**kw)
    inbox._task_creator = creator
    inbox.act(sid, "plain")
    assert seen["mid"]["state"] == "open" and seen["mid"]["outcome"] is None and seen["mid"]["task"] is None
    assert inbox.outcome("chore:C8")["state"] == "resolved"
    # recording the id fails after the card was queued: the decision stands (no revert, no orphan)
    sid2 = _post_chore(inbox, "C9")
    real_side = inbox._side
    calls = {"n": 0}

    def flaky_side():
        calls["n"] += 1
        if calls["n"] > 2:  # claim (1) ok; creation; record (2, 3...) fails
            raise sqlite3.OperationalError("database is locked")
        return real_side()
    inbox._task_creator = kb
    inbox._side = flaky_side
    inbox.act(sid2, "plain")
    inbox._side = real_side
    assert len(kb.created) == 2 and db.get_session(sid2)["archived"]
    assert inbox.outcome("chore:C9")["state"] == "open"  # still 'queuing' until a retry records it
    inbox.clock = lambda: 9e9  # past the stale-claim window: a retry reuses the Kanban card
    kb.created.clear()
    item = inbox.act(sid2, "plain")
    assert item["state"] == "resolved" and inbox.outcome("chore:C9")["task"]
    assert kb.created[0]["idempotency_key"] == f"inbox:{sid2}:plain"


def test_task_card_is_a_decision_card(tenv):
    inbox, _, kb = tenv
    sid = _post_chore(inbox, "C4")
    inbox.act(sid, "plain")
    again = inbox.post("Chore C4", "", type="chore", key="chore:C4", fields={"id": "C4"})
    assert again["session_id"] != sid and again["updated"] is False  # new card, not a follow-up note


def test_task_creates_a_real_kanban_card_on_a_temp_board(tmp_path, monkeypatch):
    kh = tmp_path / "kanban-home"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(kh))
    for v in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_WORKSPACES_ROOT"):
        monkeypatch.delenv(v, raising=False)
    kb = pytest.importorskip("hermes_cli.kanban_db")
    from hermes_state import SessionDB
    db = SessionDB(db_path=tmp_path / "state.db")
    db._inbox_keep_open = True
    reg = ct.Registry(tmp_path, skill_dirs=[])
    reg.define("chore", TASKY)
    inbox = store.Inbox(home=tmp_path, db_factory=lambda: db, types=reg)  # default creator = core API
    sid = _post_chore(inbox, "C5")
    item = inbox.act(sid, "skip", "real")
    tid = item["outcome_task"]
    from hermes_cli import kanban_db_connect as kbc
    with kbc.connect_closing() as conn:
        rows = conn.execute("SELECT id, title, body, assignee, status FROM tasks").fetchall()
    assert [r["id"] for r in rows] == [tid]  # exactly one, on the temp board
    r = rows[0]
    assert r["body"] == "Mark C5 skipped (chore:C5): real" and r["assignee"] == "helper"
    assert r["title"] == "Chore: Skip \u2014 Chore C5" and r["status"] in ("ready", "running")
    assert (kh / "kanban.db").exists()
    # a card naming a board that does not exist fails and stays open
    reg.define("chore2", {"fields": {"id": {}}, "actions": [{"id": "go", "kind": "task", "prompt": "p",
                                                             "board": "nope"}]})
    sid2 = inbox.post("c2", "", type="chore2", fields={"id": "z"})["session_id"]
    with pytest.raises(store.TaskError, match="does not exist"):
        inbox.act(sid2, "go")
    assert [i["session_id"] for i in inbox.list()] == [sid2]
    db.close()


def test_task_tool_cli_and_rest(tenv, monkeypatch, capsys):
    inbox, _, kb = tenv
    plugin = _plugin(inbox, monkeypatch, "_inbox_plugin_task")
    ap = argparse.ArgumentParser()
    plugin._setup_cli(ap)
    sid = _post_chore(inbox, "C6")
    rc = plugin._run_cli(ap.parse_args(["act", sid, "skip", "--input", "cli"]))
    out = capsys.readouterr()
    assert rc == 0 and json.loads(out.out)["outcome_task"] == "t_fake1"
    got = json.loads(plugin.handle_outcome({"key": "chore:C6"}))
    assert got["found"] and got["task"] == "t_fake1" and got["outcome"] == "skip"
    kb.fail = RuntimeError("down")
    sid = _post_chore(inbox, "C7")
    rc = plugin._run_cli(ap.parse_args(["act", sid, "plain"]))
    assert rc == 1 and "down" in capsys.readouterr().err


def test_rest_task_act_outcome_and_failure(tmp_path, monkeypatch, skills):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    api = _load("_inbox_api_task_test", HERE / "dashboard" / "plugin_api.py")
    monkeypatch.setattr(api, "_profile", lambda: "default")
    st = api._store()
    kb = FakeKanban()
    monkeypatch.setattr(st, "kanban_task_creator", kb)
    app = fastapi.FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    assert c.put("/types/chore", json=TASKY).status_code == 200
    sid = c.post("/items", json={"title": "Chore R1", "type": "chore", "key": "chore:R1",
                                 "fields": {"id": "R1"}}).json()["session_id"]
    kb.fail = RuntimeError("kanban down")
    r = c.post(f"/items/{sid}/act", json={"action": "plain"})
    assert r.status_code == 500 and "kanban down" in r.text
    assert [i["session_id"] for i in c.get("/items").json()["items"]] == [sid]  # still open
    assert c.post(f"/items/{sid}/accept").status_code == 409  # old Desktop's Do it can't touch it
    kb.fail = None
    r = c.post(f"/items/{sid}/act", json={"action": "plain"})
    assert r.status_code == 200 and r.json()["outcome_task"] == "t_fake1" and r.json()["state"] == "resolved"
    out = c.get("/outcome", params={"key": "chore:R1"}).json()
    assert out["found"] and out["task"] == "t_fake1" and len(kb.created) == 1


def test_task_act_on_another_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    api = _load("_inbox_api_task_prof", HERE / "dashboard" / "plugin_api.py")
    monkeypatch.setattr(api, "_profile", lambda: "default")
    st = api._store()
    kb = FakeKanban()
    monkeypatch.setattr(st, "kanban_task_creator", kb)
    prof = tmp_path / "profiles" / "alpha"
    prof.mkdir(parents=True)
    reg = st.types_module().Registry(prof, skill_dirs=[])
    reg.define("chore", TASKY)
    inbox = st.Inbox.for_home(prof, types=reg)
    sid = inbox.post("x", "", type="chore", key="chore:P1", fields={"id": "P1"})["session_id"]
    app = fastapi.FastAPI()
    app.include_router(api.router)
    c = TestClient(app)
    r = c.post(f"/items/{sid}/act?profile=alpha", json={"action": "plain"})
    assert r.status_code == 200 and r.json()["outcome_task"] == "t_fake1", r.text
    assert c.get("/outcome", params={"key": "chore:P1", "profile": "alpha"}).json()["task"] == "t_fake1"
    assert len(kb.created) == 1 and kb.created[0]["assignee"] == "default"


# ── REST ───────────────────────────────────────────────────────────────────
@pytest.fixture
def client(tmp_path, monkeypatch, skills):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    api = _load("_inbox_api_types_test", HERE / "dashboard" / "plugin_api.py")
    monkeypatch.setattr(api, "_profile", lambda: "default")
    st = api._store()
    reg_cls = st.types_module().Registry
    monkeypatch.setattr(st.types_module(), "_default_skill_dirs", lambda home: [skills])
    app = fastapi.FastAPI()
    app.include_router(api.router)
    return TestClient(app), api, tmp_path, reg_cls


def test_rest_items_act_outcome(client):
    c, api, root, _ = client
    assert c.get("/ping").json() == {"ok": True, "api": 3}
    r = c.post("/items", json={"title": "X post pending: Q1", "body": "", "type": "x-post", "key": "x-post:Q1",
                               "fields": {"id": "Q1", "text": "draft"}})
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    [item] = c.get("/items").json()["items"]
    assert item["type"] == "x-post" and item["fields"]["text"] == "draft"
    assert [a["id"] for a in item["actions"]] == ["approve", "edit", "skip", "rewrite"]
    assert "fields_json" not in item and "actions_json" not in item
    assert c.post(f"/items/{sid}/act", json={"action": "nope"}).status_code == 400
    assert c.post(f"/items/{sid}/act", json={"action": "edit", "input": 5}).status_code == 400
    r = c.post(f"/items/{sid}/act", json={"action": "edit", "input": "new draft"})
    assert r.status_code == 200 and r.json()["outcome"] == "edit"
    out = c.get("/outcome", params={"key": "x-post:Q1"}).json()
    assert out["found"] and out["outcome"] == "edit" and out["value"] == "new draft"
    assert c.post(f"/items/{sid}/act", json={"action": "skip"}).status_code == 409  # closed
    assert c.post("/items/nope/act", json={"action": "skip"}).status_code == 404
    assert c.get("/outcome", params={"key": "zzz"}).json() == {"found": False, "key": "zzz"}
    assert c.post("/items", json={"title": "t", "type": "x-post", "fields": {"id": "Q"}}).status_code == 400


def test_rest_prompt_action_and_accept_alias(client):
    c, api, root, _ = client
    sid = c.post("/items", json={"title": "x", "type": "x-post", "key": "x-post:Q2",
                                 "fields": {"id": "Q2", "text": "d"}}).json()["session_id"]
    r = c.post(f"/items/{sid}/accept")  # an older Desktop's Do it on a decision card
    assert r.status_code == 409 and "own actions" in r.text
    r = c.post(f"/items/{sid}/act", json={"action": "rewrite", "input": "punchier"})
    assert r.json()["prompt"].startswith("Rewrite X draft Q2: punchier.")
    legacy = c.post("/items", json={"title": "y", "body": "b", "accept_prompt": "go"}).json()["session_id"]
    assert c.post(f"/items/{legacy}/accept").json()["accept_prompt"] == "go"


def test_rest_act_and_outcome_by_profile(client):
    c, api, root, reg_cls = client
    st = api._store()
    prof = root / "profiles" / "alpha"
    prof.mkdir(parents=True)
    sid = st.Inbox.for_home(prof).post("x", "", type="x-post", key="x-post:P1",
                                       fields={"id": "P1", "text": "t"})["session_id"]
    assert c.post(f"/items/{sid}/act", json={"action": "approve"}).status_code == 404  # wrong profile
    r = c.post(f"/items/{sid}/act?profile=alpha", json={"action": "approve"})
    assert r.status_code == 200 and r.json()["state"] == "resolved"
    assert c.get("/outcome", params={"key": "x-post:P1", "profile": "alpha"}).json()["outcome"] == "approve"
    assert c.get("/outcome", params={"key": "x-post:P1"}).json()["found"] is False
    assert c.post(f"/items/{sid}/act?profile=../etc", json={"action": "approve"}).status_code == 400


def test_rest_types_define_and_traversal(client):
    c, api, root, _ = client
    names = {t["name"] for t in c.get("/types").json()["types"]}
    assert names == {"default", "x-post"}
    assert c.get("/types/x-post").json()["source"] == "skill:x-presence"
    assert c.get("/types/nope").status_code == 404
    r = c.put("/types/yes-no", json={"spec": "actions: [{id: ok, kind: resolve}]"})
    assert r.status_code == 200 and r.json()["source"] == "local"
    assert (root / "inbox" / "types" / "yes-no.yaml").exists()
    r = c.put("/types/yes-no", json={"actions": [{"id": "y", "kind": "resolve"}]})  # bare spec body
    assert r.status_code == 200 and r.json()["actions"][0]["id"] == "y"
    for bad in ("..%2Fescape", "UPPER", "a.b"):
        assert c.put(f"/types/{bad}", json={"actions": [{"id": "a", "kind": "resolve"}]}).status_code in (400, 404)
    assert not list(root.glob("**/escape*"))
    assert c.put("/types/bad", json={"actions": [{"id": "a", "kind": "task"}]}).status_code == 400
    assert c.put("/types/bad", json={"spec": "actions: [{id: a"}).status_code == 400  # YAML syntax error
    assert c.put("/types/default", json={"actions": [{"id": "a", "kind": "resolve"}]}).status_code == 400
    assert c.put("/types/x-post", json={"actions": [{"id": "a", "kind": "resolve"}]}).status_code == 400
    assert c.put("/types/evil%0A", json={"actions": [{"id": "a", "kind": "resolve"}]}).status_code == 400

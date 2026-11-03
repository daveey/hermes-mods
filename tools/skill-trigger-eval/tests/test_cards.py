from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import cards as cl  # noqa: E402

GOOD = {"skill": "a", "should_load": [{"ask": "x"}, {"ask": "y"}, {"ask": "z"}],
        "near_miss": [{"ask": "n", "instead": "b"}], "reviewed": False}


def test_validate_accepts_good_card():
    c = cl.validate(GOOD, {"a", "b"})
    assert [x[:2] for x in c.asks()] == [("a#s0", "should"), ("a#s1", "should"), ("a#s2", "should"),
                                         ("a#n0", "near_miss")]
    assert list(c.asks())[-1][3] == "b"


@pytest.mark.parametrize("mut,msg", [
    (lambda d: d.update(should_load=d["should_load"][:2]), ">= 3 should_load"),
    (lambda d: d.update(near_miss=[]), ">= 1 near_miss"),
    (lambda d: d["near_miss"][0].update(instead="a"), "names itself"),
    (lambda d: d["near_miss"][0].update(instead="zzz"), "not a skill in the index"),
    (lambda d: d["should_load"][0].update(ask=" "), "without text"),
    (lambda d: d.pop("skill"), "missing skill"),
])
def test_validate_rejects(mut, msg):
    d = json.loads(json.dumps(GOOD))
    mut(d)
    with pytest.raises(cl.CardError, match=msg):
        cl.validate(d, {"a", "b"})


def test_null_instead_is_allowed():
    d = json.loads(json.dumps(GOOD))
    d["near_miss"][0]["instead"] = None
    assert cl.validate(d, {"a"}).near_miss[0]["instead"] is None


def test_card_locations_owned_vs_bundled(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "HARNESS_CARDS", tmp_path / "cards")
    owned_md = tmp_path / "skills" / "dev" / "a" / "SKILL.md"
    owned_md.parent.mkdir(parents=True)
    owned_md.write_text("---\nname: a\n---\n")
    assert cl.card_path_for("a", owned_md, True) == owned_md.parent / "references" / "trigger-cards.yaml"
    assert cl.card_path_for("b", tmp_path / "x" / "SKILL.md", False) == tmp_path / "cards" / "b.yaml"


def test_load_cards_prefers_skill_dir_and_falls_back_to_harness(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "HARNESS_CARDS", tmp_path / "cards")
    (tmp_path / "cards").mkdir()
    a_md = tmp_path / "s" / "a" / "SKILL.md"
    (a_md.parent / "references").mkdir(parents=True)
    a_md.write_text("x")
    (a_md.parent / "references" / "trigger-cards.yaml").write_text(cl.dump_card(GOOD))
    b = dict(GOOD, skill="b", near_miss=[{"ask": "n", "instead": None}])
    (tmp_path / "cards" / "b.yaml").write_text(cl.dump_card(b))
    cards, missing = cl.load_cards({"a": a_md, "b": tmp_path / "t" / "b" / "SKILL.md", "c": tmp_path / "c.md"},
                                   owned={"a", "b"}, known={"a", "b", "c"})
    assert set(cards) == {"a", "b"} and missing == ["c"]
    assert cards["a"].path == a_md.parent / "references" / "trigger-cards.yaml"
    assert cards["b"].path == tmp_path / "cards" / "b.yaml"


def test_parse_generated_strips_fence_and_normalizes_null():
    text = "```yaml\nskill: wrong\nshould_load:\n- ask: one\n- ask: two\n- ask: three\nnear_miss:\n- ask: n\n  instead: none\n```"
    d = cl.parse_generated(text, "a", {"a", "b"})
    assert d["skill"] == "a" and d["near_miss"][0]["instead"] is None and d["reviewed"] is False


def test_bundled_names_reads_manifest_and_core_trees(tmp_path):
    home, core = tmp_path / "h", tmp_path / "c"
    (home / "skills").mkdir(parents=True)
    (home / "skills" / ".bundled_manifest").write_text("arxiv:abc\npdf:def\n")
    (core / "optional-skills" / "x" / "opt").mkdir(parents=True)
    (core / "optional-skills" / "x" / "opt" / "SKILL.md").write_text("x")
    assert cl.bundled_names(home, core) == {"arxiv", "pdf", "opt"}


def test_mine_phrasings_takes_the_ask_before_the_first_skill_view(tmp_path):
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute("create table sessions (id text, source text)")
    con.execute("create table messages (id integer primary key, session_id text, role text, content text, tool_calls text)")
    con.executemany("insert into sessions values (?,?)", [("s1", "desktop"), ("s2", "kanban")])
    tc = lambda name, **kw: json.dumps([{"function": {"name": "skill_view", "arguments": json.dumps({"name": name, **kw})}}])
    con.executemany("insert into messages (session_id, role, content, tool_calls) values (?,?,?,?)", [
        ("s1", "user", "my mac disk is completely full again", None),
        ("s1", "assistant", "", tc("mac-disk-cleanup")),
        ("s1", "assistant", "", tc("later-skill")),          # not the first reply after the ask
        ("s1", "user", "[system note] ignore", None),        # bracketed system-ish rows skipped
        ("s1", "assistant", "", tc("x")),
        ("s1", "user", "read the ref", None),
        ("s1", "assistant", "", tc("y", file_path="references/a.md")),  # reference loads don't count
        ("s2", "user", "kanban body", None),                  # non-human source skipped
        ("s2", "assistant", "", tc("z")),
        ("s1", "user", "do it", None),                        # too short to stand alone
        ("s1", "assistant", "", tc("short-ctx")),
    ])
    con.commit()
    con.close()
    assert cl.mine_phrasings(db) == {"mac-disk-cleanup": ["my mac disk is completely full again"]}

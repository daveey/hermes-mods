"""Curriculum (Phase 3): loading + overlay, live triggers, spaced repetition, retirement on adoption, the miner's
adoption tests, and that every command a tip names exists in the current Hermes build."""
import importlib.util
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MINE = ROOT.parents[1] / "skills" / "productivity" / "hermes-coach" / "scripts" / "coach_mine.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


core = load("coach_core_test_c", ROOT / "core.py")
cur = core.curriculum_mod()
mine = load("coach_mine_test", MINE)

NOW = 1_800_000_000.0


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


def make_db(tmp_path, sid="s1", cwd="/code/app", source="desktop", others=(), tool_calls=()):
    """state.db with the session plus `others`: (id, cwd, started_at, last_activity_at, ended_at, source)."""
    p = tmp_path / "state.db"
    con = sqlite3.connect(p)
    con.execute("create table sessions (id text, title text, cwd text, git_branch text, git_repo_root text, model text,"
                " profile_name text, parent_session_id text, end_reason text, source text, started_at real,"
                " last_activity_at real, ended_at real)")
    con.execute("create table messages (id integer primary key, session_id text, role text, content text,"
                " tool_calls text, display_kind text)")
    con.execute("insert into sessions values (?,'App',?,null,?,'m','default',null,null,?,?,?,null)",
                (sid, cwd, cwd, source, NOW - 60, NOW - 10))
    for o in others:
        oid, ocwd, st, act, end, src = o
        con.execute("insert into sessions values (?,'x',?,null,?,'m','default',null,null,?,?,?,?)",
                    (oid, ocwd, ocwd, src, st, act, end))
    for name in tool_calls:
        con.execute("insert into messages (session_id, role, content, tool_calls) values (?,'assistant','',?)",
                    (sid, json.dumps([{"type": "function", "function": {"name": name, "arguments": "{}"}}])))
    con.commit()
    con.close()
    return p


def mk(tmp_path, overlay=None, adoption=None, monkeypatch=None, clock=None):
    root = tmp_path / "coach"
    root.mkdir(exist_ok=True)
    if overlay is not None:
        (root / "curriculum.yaml").write_text(overlay)
    ad = tmp_path / "adoption.json"
    ad.write_text(json.dumps({"entries": adoption or {}}))
    monkeypatch.setenv("COACH_ADOPTION", str(ad))
    return core.Store(root, clock or Clock())


def turn(store, sid, text):
    store.user_turn(sid, text)


def st(store, sid, db):
    return core.state(store, sid, db)


# ── loading ──────────────────────────────────────────────────────────────

def test_public_curriculum_has_at_least_10_runnable_entries_with_kanban_l1_to_l4():
    es = cur.load(None)
    ids = [e["id"] for e in es]
    assert len(ids) == len(set(ids)) >= 10
    assert all(cur.runnable(e) for e in es), [e["id"] for e in es if not cur.runnable(e)]
    for lvl in ("l1", "l2", "l3", "l4"):
        assert any(i.startswith(f"kanban_{lvl}") for i in ids), lvl
    for seed in ("project_bot", "kanban_max_runtime", "todo_checklist", "cron_whenever", "worktree_parallel",
                 "session_mention"):
        assert seed in ids


def test_overlay_overrides_by_id_adds_and_disables(tmp_path):
    ov = tmp_path / "ov.yaml"
    ov.write_text("- id: cron_whenever\n  tip: mine\n- id: todo_checklist\n  enabled: false\n"
                  "- feature: prose_only\n  trigger: when it feels right\n  tip: x\n  adopted: used it 3x\n")
    es = {e["id"]: e for e in cur.load(ov)}
    assert es["cron_whenever"]["tip"] == "mine" and "trigger" in es["cron_whenever"]  # shallow merge keeps the rest
    assert "todo_checklist" not in es
    assert "prose_only" in es and not cur.runnable(es["prose_only"])


# ── live triggers → one tip on the bus ───────────────────────────────────

def test_text_trigger_shows_tip_with_try_button_and_logs_it(tmp_path, monkeypatch):
    store = mk(tmp_path, monkeypatch=monkeypatch)
    db = make_db(tmp_path)
    turn(store, "s1", "can you check the build every morning and tell me")
    r = st(store, "s1", db)
    n = r["nudge"]
    assert n["rule"] == "tip:cron_whenever" and n["action"] == "tip" and n["button"] == "Try it"
    assert n["title"].startswith("Tip · ") and "hermes cron create" in n["text"] and n["data"]["try"]
    assert any(x.get("event") == "shown" and x.get("rule") == "tip:cron_whenever" for x in store.read_log())
    s = json.loads((store.root / "curriculum_state.json").read_text())
    assert s["tips"]["cron_whenever"]["shows"] == 1


def test_text_trigger_expires_after_two_turns(tmp_path, monkeypatch):
    store = mk(tmp_path, monkeypatch=monkeypatch)
    db = make_db(tmp_path)
    turn(store, "s1", "whenever it fails, retry")
    turn(store, "s1", "ok")
    turn(store, "s1", "and then?")
    assert st(store, "s1", db)["nudge"] is None


def test_tip_ranks_below_bus_rules(tmp_path, monkeypatch):
    store = mk(tmp_path, monkeypatch=monkeypatch)
    db = make_db(tmp_path)
    for _ in range(3):
        turn(store, "s1", "still not working, run it every day")
    assert st(store, "s1", db)["nudge"]["rule"] == "still_broken"


def test_signal_triggers_long_job_subagents_still_broken(tmp_path, monkeypatch):
    store = mk(tmp_path, monkeypatch=monkeypatch)
    db = make_db(tmp_path)
    turn(store, "s1", "go")
    store.long_job("s1", "terminal ran 12 min")
    # long_job also fires the bus's own move_to_board first; dismiss it to free the 15-turn budget in a new session
    r = st(store, "s1", db)
    assert r["nudge"]["rule"] == "move_to_board"
    (tmp_path / "b").mkdir()
    store2 = mk(tmp_path / "b", monkeypatch=monkeypatch)
    db2 = make_db(tmp_path / "b")
    turn(store2, "s1", "go")
    store2.add_subagents("s1", 5)
    assert st(store2, "s1", db2)["nudge"]["rule"] == "tip:kanban_l2_fanout"


def test_list_items_trigger_respects_unless_tool(tmp_path, monkeypatch):
    checklist = "please:\n1. bump the version\n2. run the tests\n3. push the branch"
    store = mk(tmp_path, monkeypatch=monkeypatch)
    turn(store, "s1", checklist)
    assert st(store, "s1", make_db(tmp_path))["nudge"]["rule"] == "tip:todo_checklist"
    (tmp_path / "b").mkdir()
    store = mk(tmp_path / "b", monkeypatch=monkeypatch)
    turn(store, "s1", checklist)
    db = make_db(tmp_path / "b", tool_calls=["todo_list"])
    assert st(store, "s1", db)["nudge"] is None


def test_project_sessions_and_concurrent_triggers_fill_project(tmp_path, monkeypatch):
    others = [(f"o{i}", "/code/babybear", NOW - 86400 * i, NOW - 86400 * i, NOW - 86400 * i + 60, "desktop")
              for i in range(1, 6)]
    store = mk(tmp_path, monkeypatch=monkeypatch)
    turn(store, "s1", "go")
    n = st(store, "s1", make_db(tmp_path, cwd="/code/babybear", others=others))["nudge"]
    assert n["rule"] == "tip:project_bot" and "#6 on babybear" in n["text"] and "profile create babybear" in n["text"]
    # machine sessions and old sessions don't count
    (tmp_path / "b").mkdir()
    store = mk(tmp_path / "b", monkeypatch=monkeypatch)
    turn(store, "s1", "go")
    few = others[:2] + [("k1", "/code/babybear", NOW - 100, NOW - 50, None, "kanban"),
                        ("old", "/code/babybear", NOW - 30 * 86400, NOW - 30 * 86400, NOW, "desktop")]
    db = make_db(tmp_path / "b", cwd="/code/babybear", others=few)
    assert st(store, "s1", db)["nudge"] is None
    # a second live session in the same repo -> worktrees
    (tmp_path / "c").mkdir()
    store = mk(tmp_path / "c", monkeypatch=monkeypatch)
    turn(store, "s1", "go")
    live = [("p2", "/code/app", NOW - 600, NOW - 120, None, "desktop")]
    n = st(store, "s1", make_db(tmp_path / "c", others=live))["nudge"]
    assert n["rule"] == "tip:worktree_parallel" and "/code/app" in n["text"]


def test_overlay_match_routes_babybear_to_its_own_bot_tip(tmp_path, monkeypatch):
    overlay = (
        "- id: project_bot\n  trigger:\n    project_sessions: {n: 6, days: 14, match: '^(?!.*babybear)'}\n"
        "- id: babybear_bot\n  feature: Make a Baby Bear Bot\n  trigger:\n    project_sessions: {n: 5, days: 14, match: babybear}\n"
        "  tip: 'Baby Bear session #{n}'\n  adopted: {unit: sessions, min: 3, profile: '^bb-dev$'}\n")
    others = [(f"o{i}", "/code/babybear", NOW - 86400 * i, NOW - 86400 * i, NOW, "desktop") for i in range(1, 6)]
    store = mk(tmp_path, overlay=overlay, monkeypatch=monkeypatch)
    turn(store, "s1", "go")
    n = st(store, "s1", make_db(tmp_path, cwd="/code/babybear", others=others))["nudge"]
    assert n["rule"] == "tip:babybear_bot" and n["text"] == "Baby Bear session #6" and n["button"] == "Got it"


# ── spaced repetition + retirement ───────────────────────────────────────

def test_dismissal_backs_off_exponentially_and_accept_waits(tmp_path, monkeypatch):
    clock = Clock()
    store = mk(tmp_path, monkeypatch=monkeypatch, clock=clock)
    sp = cur.Spacing(store.root, clock)
    sp.on_shown("cron_whenever")
    assert not sp.ready("cron_whenever")
    clock.t += cur.SHOW_GAP_S + 1
    assert sp.ready("cron_whenever")
    store.record("s1", "tip:cron_whenever", "dismissed")
    gaps = []
    for _ in range(3):
        r = sp.read()["tips"]["cron_whenever"]
        gaps.append(round((r["next_at"] - clock.t) / 86400))
        clock.t = r["next_at"] + 1
        store.record("s1", "tip:cron_whenever", "dismissed")
    assert gaps == [2, 4, 8]
    store.record("s1", "tip:todo_checklist", "accepted")
    assert sp.read()["tips"]["todo_checklist"]["next_at"] == clock.t + cur.ACCEPT_GAP_S


def test_tip_not_reshown_in_other_session_until_due_then_shown(tmp_path, monkeypatch):
    clock = Clock()
    store = mk(tmp_path, monkeypatch=monkeypatch, clock=clock)
    db = make_db(tmp_path)
    turn(store, "s1", "do this every day")
    assert st(store, "s1", db)["nudge"]["rule"] == "tip:cron_whenever"
    store.record("s1", "tip:cron_whenever", "dismissed")
    (tmp_path / "x").mkdir()
    turn(store, "s1", "whenever")  # same session: muted + already shown
    assert st(store, "s1", db)["nudge"] is None
    # other session, the next day: still backing off (2 days after one dismissal)
    clock.t += 86400
    sdb = make_db(tmp_path / "x", sid="s2")
    turn(store, "s2", "every morning please")
    assert st(store, "s2", sdb)["nudge"] is None
    clock.t += 86400 + 10
    turn(store, "s2", "every morning please")
    assert st(store, "s2", sdb)["nudge"]["rule"] == "tip:cron_whenever"


def test_adopted_tip_retires(tmp_path, monkeypatch):
    store = mk(tmp_path, monkeypatch=monkeypatch, adoption={"cron_whenever": {"adopted": True}})
    db = make_db(tmp_path)
    turn(store, "s1", "every day at 9")
    assert st(store, "s1", db)["nudge"] is None
    assert cur.Spacing(store.root).read()["tips"]["cron_whenever"]["retired_at"]


def test_daily_cap_across_sessions(tmp_path, monkeypatch):
    store = mk(tmp_path, monkeypatch=monkeypatch)
    for i in range(cur.DAILY_MAX):
        cur.Spacing(store.root, store.clock).on_shown(f"t{i}")
    turn(store, "s1", "every day")
    assert st(store, "s1", make_db(tmp_path))["nudge"] is None


def test_expired_tip_frees_spacing_and_stats_count_tips(tmp_path, monkeypatch):
    store = mk(tmp_path, monkeypatch=monkeypatch)
    turn(store, "s1", "every day")
    n = st(store, "s1", make_db(tmp_path))["nudge"]
    store.record("s1", n["rule"], "expired", n["id"])
    assert cur.Spacing(store.root, store.clock).ready("cron_whenever")
    store.record("s1", n["rule"], "accepted", n["id"])
    assert core.stats(store)["rules"]["tip:cron_whenever"]["shown"] == 1


def test_expired_drops_only_its_own_show_from_daily_count(tmp_path):
    clock = Clock()
    sp = cur.Spacing(tmp_path, clock)
    sp.on_shown("a")
    clock.t += 60
    sp.on_shown("b")
    sp.on_choice("a", "expired")  # a's show goes; b's (the latest) stays
    assert sp.read()["shows"] == [NOW + 60]


def test_muted_first_tip_lets_next_due_tip_through(tmp_path, monkeypatch):
    store = mk(tmp_path, monkeypatch=monkeypatch)
    store._update("s1", lambda x: x.setdefault("muted", []).append("tip:cron_whenever"))
    turn(store, "s1", "every day, and keep the follow-ups for later")
    n = st(store, "s1", make_db(tmp_path))["nudge"]
    assert n and n["rule"] == "tip:kanban_l1_boards", n


# ── miner: adoption tests over digests ───────────────────────────────────

def dg(i, src="desktop", features=None, signals=None, tools=None, profile="default", cwd="~/code/app", msgs=50, age=1,
       repo=None):
    return {"host": "h", "profile": profile, "id": f"d{i}", "source": src, "cwd": cwd, "repo": repo or cwd, "title": "t",
            "msgs": msgs, "user_turns_total": 3, "started_at": NOW - age * 86400, "last_activity_at": NOW - age * 86400,
            "features": features or {}, "signals": signals or {}, "tools": tools or {}}


def test_miner_evaluates_events_sessions_sources_any_and_window():
    es = {e["id"]: e for e in mine.load_curriculum([ROOT / "curriculum.yaml"])}
    ds = [dg(1, features={"kanban_create:max_runtime": 4}),
          dg(2, features={"kanban_create:max_runtime": 1}),
          dg(3, src="kanban", features={"kanban_create:max_runtime": 1}),
          dg(4, features={"kanban_create:max_runtime": 1}, age=30)]
    r = mine.adoption([es["kanban_max_runtime"]], ds, now=NOW)["kanban_max_runtime"]
    assert (r["count"], r["need"], r["adopted"]) == (2, 3, False)  # 2 interactive sessions in 14 d; kanban + old skipped
    ds.append(dg(5, features={"kanban_create:max_runtime": 1}))
    r = mine.adoption([es["kanban_max_runtime"]], ds, now=NOW)["kanban_max_runtime"]
    assert r["adopted"] and r["evidence"][0].startswith("h/default/")
    # any-of: worktree sessions by cwd OR worktree cards
    wt = [dg(6, cwd="~/code/app/.worktrees/a", repo="~/code/app"), dg(7, cwd="~/code/app/.worktrees/b", repo="~/code/app")]
    assert mine.adoption([es["worktree_parallel"]], wt, now=NOW)["worktree_parallel"]["adopted"]
    wt = [dg(8, features={"kanban_create:worktree": 2})]
    assert mine.adoption([es["worktree_parallel"]], wt, now=NOW)["worktree_parallel"]["adopted"]
    # profile regex (a Bot = non-default profile with interactive sessions)
    bots = [dg(10 + i, profile="babybear-dev") for i in range(3)]
    assert mine.adoption([es["project_bot"]], bots, now=NOW)["project_bot"]["adopted"]
    assert not mine.adoption([es["project_bot"]], [dg(20 + i) for i in range(5)], now=NOW)["project_bot"]["adopted"]


def test_miner_counts_sibling_linked_worktree_sessions(tmp_path):
    """The user's real habit: `git worktree add ../babybear-x`, so cwd=~/code/babybear-x, repo (git common
    root)=~/code/babybear. Subdirectories of the main checkout are not worktrees; machine sessions don't count."""
    ov = tmp_path / "ov.yaml"
    ov.write_text("- id: babybear_worktrees\n  adopted:\n    any:\n      - {unit: sessions, min: 2, linked_worktree: true,"
                  " match: babybear, sources: interactive}\n")  # same shape as the private overlay entry
    es = {e["id"]: e for e in mine.load_curriculum([ROOT / "curriculum.yaml", ov])}
    assert mine.linked_worktree({"cwd": "~/code/babybear-logship", "repo": "~/code/babybear"})
    assert mine.linked_worktree({"cwd": "~/code/app/.worktrees/a", "repo": "~/code/app"})
    assert not mine.linked_worktree({"cwd": "~/code/babybear/app", "repo": "~/code/babybear"})
    assert not mine.linked_worktree({"cwd": "~/code/babybear", "repo": "~/code/babybear"})
    assert not mine.linked_worktree({"cwd": "~/code/x", "repo": None})
    sub = [dg(1, cwd="~/code/babybear/app", repo="~/code/babybear"), dg(2, cwd="~/code/babybear", repo="~/code/babybear"),
           dg(3, src="cron", cwd="~/code/babybear-a", repo="~/code/babybear")]
    for tip in ("worktree_parallel", "babybear_worktrees"):
        assert not mine.adoption([es[tip]], sub, now=NOW)[tip]["adopted"]
    wt = sub + [dg(4, cwd="~/code/babybear-logship", repo="~/code/babybear"),
                dg(5, cwd="~/code/babybear-ear-faces", repo="~/code/babybear")]
    for tip in ("worktree_parallel", "babybear_worktrees"):
        r = mine.adoption([es[tip]], wt, now=NOW)[tip]
        assert r["adopted"] and set(r["evidence"]) == {"h/default/d4", "h/default/d5"}, (tip, r)
    # a sibling worktree of another repo does not satisfy the Baby Bear entry
    other = [dg(6, cwd="~/code/app-x", repo="~/code/app"), dg(7, cwd="~/code/app-y", repo="~/code/app")]
    assert mine.adoption([es["worktree_parallel"]], other, now=NOW)["worktree_parallel"]["adopted"]
    assert not mine.adoption([es["babybear_worktrees"]], other, now=NOW)["babybear_worktrees"]["adopted"]


def test_miner_adoption_is_sticky_and_prose_entries_are_unchecked(tmp_path):
    es = mine.load_curriculum([ROOT / "curriculum.yaml"])
    ds = [dg(i, features={"cron_create": 1}) for i in range(3)]
    res = mine.write_adoption(tmp_path, es, ds, now=NOW)
    assert res["cron_whenever"]["adopted"] and res["cron_whenever"]["first_adopted"]
    res2 = mine.write_adoption(tmp_path, es + [{"id": "prose", "adopted": "used it 3x"}], [], now=NOW + 30 * 86400)
    assert res2["cron_whenever"]["adopted"] and res2["cron_whenever"]["count"] == 0
    assert res2["cron_whenever"]["first_adopted"] == res["cron_whenever"]["first_adopted"]
    assert res2["prose"]["checked"] is False
    # the plugin reads the file the miner wrote
    assert "cron_whenever" in cur.adopted_ids(tmp_path / "reports" / "adoption.json")


def test_miner_overlay_files_from_data_repo(tmp_path):
    (tmp_path / "curriculum").mkdir()
    (tmp_path / "curriculum" / "mac.yaml").write_text("- id: extra\n  tip: t\n  trigger: {long_job: true}\n"
                                                      "  adopted: {feature: todo}\n")
    files = mine.curriculum_files(tmp_path, [str(ROOT / "curriculum.yaml")])
    assert files[-1].name == "mac.yaml"
    assert "extra" in {e["id"] for e in mine.load_curriculum(files)}


# ── every command a tip names exists in the current build ─────────────────

CMD_RE = re.compile(r"`(hermes [a-z][a-z -]*?)(?: <[^>]+>| --[a-z-]+| [A-Z{][^`]*)*`")


def test_every_hermes_command_in_a_tip_is_declared():
    for e in cur.load(None):
        named = {m.group(1).strip() for m in CMD_RE.finditer(str(e["tip"]))}
        named = {re.sub(r"\s+\{.*$", "", n) for n in named}
        declared = set(e.get("commands") or [])
        missing = {n for n in named if not any(n == d or n.startswith(d + " ") or d.startswith(n) for d in declared)}
        assert not missing, (e["id"], missing)


@pytest.mark.skipif(not shutil.which("hermes"), reason="hermes CLI not on PATH")
def test_declared_commands_exist_in_current_build():
    cmds = sorted({c for e in cur.load(None) for c in e.get("commands") or []} | {"hermes kanban create"})
    for c in cmds:
        r = subprocess.run(c.split() + ["--help"], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0 and "usage" in (r.stdout + r.stderr).lower(), (c, r.stderr[-300:])
    # flags the tips name
    k = subprocess.run(["hermes", "kanban", "create", "--help"], capture_output=True, text=True, timeout=60).stdout
    assert "--triage" in k and "--max-runtime" in k
    top = subprocess.run(["hermes", "--help"], capture_output=True, text=True, timeout=60).stdout
    assert "--worktree" in top and "-w" in top

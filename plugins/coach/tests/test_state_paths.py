"""Coach production DB paths resolve the home they are given, like core, never the real ~/.hermes behind it.

state.db: an explicit `home` argument, else core get_hermes_home() (task override > HERMES_HOME > platform default).
Board enumeration (stalled_goal): an explicit home, else core kanban_home() (HERMES_KANBAN_HOME > default Hermes
root); HERMES_KANBAN_DB, a worker's one-board pin, never relabels or hides the default board.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest
from hermes_constants import reset_hermes_home_override, set_hermes_home_override

HERE = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(f"t_state_paths_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


sg = _load("stalled_goal")
sh = _load("self_heal")
rc = _load("reply_check")
gs = _load("goal_scope")


@pytest.fixture
def override():
    tokens = []

    def set_(path):
        tokens.append(set_hermes_home_override(str(path)))

    yield set_
    for t in reversed(tokens):
        reset_hermes_home_override(t)


def _board(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(path).close()
    return path


def _state(home: Path, sessions=(), messages=()) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(home / "state.db")
    c.execute("create table sessions (id text primary key, source text, parent_session_id text)")
    c.execute("create table messages (id integer primary key, session_id text, role text, content text, "
              "timestamp real)")
    c.executemany("insert into sessions values (?,?,?)", sessions)
    c.executemany("insert into messages (session_id, role, content, timestamp) values (?,?,?,?)", messages)
    c.commit()
    c.close()
    return home / "state.db"


# ── stalled_goal board enumeration ──

def test_board_dbs_follow_kanban_home_not_hermes_home(tmp_path, monkeypatch):
    decoy, root = tmp_path / "decoy", tmp_path / "kroot"
    _board(decoy / "kanban.db")
    default, named = _board(root / "kanban.db"), _board(root / "kanban" / "boards" / "ops" / "kanban.db")
    monkeypatch.setenv("HERMES_HOME", str(decoy))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(root))
    assert sg.board_dbs() == [(None, default, True), ("ops", named, True)]


def test_board_dbs_of_a_profile_home_use_the_shared_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    _board((root / "profiles" / "work") / "kanban.db")  # a stray per-profile file is not the shared board
    default = _board(root / "kanban.db")
    monkeypatch.delenv("HERMES_KANBAN_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(root / "profiles" / "work"))
    assert sg.board_dbs() == [(None, default, True)]


def test_kanban_db_pin_neither_relabels_nor_hides_the_default_board(tmp_path, monkeypatch):
    default, named = _board(tmp_path / "kanban.db"), _board(tmp_path / "kanban" / "boards" / "ops" / "kanban.db")
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_DB", str(named))
    assert sg.board_dbs() == [(None, default, True), ("ops", named, True)]


def test_explicit_board_home_beats_kanban_env(tmp_path, monkeypatch):
    env_root, given = tmp_path / "env", tmp_path / "given"
    _board(env_root / "kanban.db")
    mine = _board(given / "kanban.db")
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(env_root))
    assert sg.board_dbs(given) == [(None, mine, True)]
    assert sg.kanban_root(given) == given


def test_stalled_goal_home_honors_task_override_and_explicit_home(tmp_path, monkeypatch, override):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env"))
    override(tmp_path / "task")
    assert sg.home() == tmp_path / "task"
    assert sg.home(str(tmp_path / "given")) == tmp_path / "given"


def test_find_incidents_via_hook_reads_only_the_kanban_root(tmp_path, monkeypatch):
    """session_hint's default board list (the live-read path) comes from the supplied kanban root."""
    root = tmp_path / "kroot"
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "decoy"))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(root))
    seen = []
    monkeypatch.setattr(sg, "enabled", lambda: True)
    monkeypatch.setattr(sg, "find_stalls", lambda dbs, **kw: seen.append(list(dbs)) or [])
    _board(root / "kanban.db")
    _board(tmp_path / "decoy" / "kanban.db")
    assert sg.session_hint(["sid-unique-for-paths"], tmp_path / "coach", now=1.0) is None
    assert seen == [[(None, root / "kanban.db", True)]]


# ── self_heal probe lookup (state.db) ──

PROBE = "[selfheal-probe] break the detector on purpose"


def test_probe_session_reads_the_task_override_home(tmp_path, monkeypatch, override):
    real, task = tmp_path / "real", tmp_path / "task"
    _state(real, messages=[("S", "user", "an ordinary request", 1.0)])
    _state(task, messages=[("S", "user", PROBE, 1.0)])
    monkeypatch.setenv("HERMES_HOME", str(real))
    assert not sh.probe_defect({"session_id": "S"})
    override(task)
    assert sh.probe_defect({"session_id": "S"})
    assert not sh.probe_session("S", home=real)  # an explicit home stays authoritative


def test_probe_session_reads_supplied_hermes_home(tmp_path, monkeypatch):
    _state(tmp_path / "h", messages=[("S", "user", PROBE, 1.0)])
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "h"))
    assert sh.probe_session("S")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "empty"))
    assert not sh.probe_session("S")


def test_self_heal_home_fallback_never_uses_cwd_for_empty_env(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_constants", None)  # core unimportable: the fallback path
    monkeypatch.setenv("HERMES_HOME", "")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert sh.hermes_home() == tmp_path / ".hermes"
    assert rc._hermes_home() == tmp_path / ".hermes"
    assert sg.home() == tmp_path / ".hermes"


# ── reply_check lineage / subagent (state.db) ──

def test_reply_check_lineage_and_subagent_read_the_task_override_home(tmp_path, monkeypatch, override):
    real, task = tmp_path / "real", tmp_path / "task"
    _state(real, sessions=[("child", "desktop", None)])
    _state(task, sessions=[("child", "subagent", "parent"), ("parent", "desktop", None)])
    monkeypatch.setenv("HERMES_HOME", str(real))
    assert not rc.is_subagent("child") and rc._lineage("child") == ["child"]
    override(task)
    assert rc.is_subagent("child") and rc._lineage("child") == ["child", "parent"]


# ── goal_scope saved steering (state.db) ──

def test_goal_scope_saved_steering_opens_the_supplied_home(tmp_path, monkeypatch, override):
    _state(tmp_path / "env")
    _state(tmp_path / "task")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env"))
    opened = []

    class Opened(Exception):
        pass

    def spy(path):
        opened.append(Path(path))
        raise Opened

    monkeypatch.setattr(gs, "_ro_connect", spy)
    with pytest.raises(Opened):
        gs._check_saved_steering("S", set())
    override(tmp_path / "task")
    with pytest.raises(Opened):
        gs._check_saved_steering("S", set())
    assert opened == [tmp_path / "env" / "state.db", tmp_path / "task" / "state.db"]

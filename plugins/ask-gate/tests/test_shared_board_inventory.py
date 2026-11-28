"""Pure-stdlib named-board inventory, without a Hermes runtime or live boards."""
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

PLUGINS = Path(__file__).resolve().parents[2]


def load(relative):
    spec = importlib.util.spec_from_file_location("inventory_test_module", PLUGINS / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def inventory():
    return load("kanban_inventory.py")


def db(root, slug, schema: str | None = "CREATE TABLE tasks(id TEXT)"):
    path = root / slug / "kanban.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        if schema:
            conn.execute(schema)
            conn.commit()
    return path


def test_missing_paths_never_create_files(tmp_path, inventory):
    missing = tmp_path / "absent"
    assert inventory.board_db_files(missing) == []
    assert not missing.exists()
    (tmp_path / "empty").mkdir()
    assert inventory.board_db_files(tmp_path) == []
    assert not (tmp_path / "empty" / "kanban.db").exists()


def test_sorted_real_boards_only(tmp_path, inventory):
    z = db(tmp_path, "z-last")
    a = db(tmp_path, "a_1")
    db(tmp_path, "zero", schema=None)
    db(tmp_path, "schema-less", schema="CREATE TABLE unrelated(id TEXT)")
    for slug in ("_archived", "_hidden", "Upper", "dot.name", "-leading", "a" * 65):
        db(tmp_path, slug)
    db(tmp_path / "_archived", "nested")
    db(tmp_path / "outer", "nested")
    (tmp_path / "not-a-dir").touch()
    (tmp_path / "directory-db" / "kanban.db").mkdir(parents=True)
    assert inventory.board_db_files(tmp_path) == [a, z]
    assert (tmp_path / "zero" / "kanban.db").stat().st_size == 0


def test_uri_metacharacters_are_escaped(tmp_path, inventory):
    root = tmp_path / "path?# space"
    path = db(root, "board")
    assert inventory.board_db_files(root) == [path]


def test_corrupt_board_raises(tmp_path, inventory):
    path = db(tmp_path, "broken", schema=None)
    path.write_bytes(b"not SQLite" * 100)
    with pytest.raises(sqlite3.DatabaseError) as caught:
        inventory.board_db_files(tmp_path)
    assert caught.value.board_db_path == path


def test_recovery_inventory_retains_corrupt_path_without_dropping_healthy_boards(tmp_path, inventory):
    broken = db(tmp_path, "broken", schema=None)
    broken.write_bytes(b"not SQLite" * 100)
    healthy = db(tmp_path, "healthy")
    assert inventory.board_db_files(tmp_path, include_unreadable=True) == [broken, healthy]


def test_shared_helper_changes_select_all_consumer_suites():
    import runpy
    selector = runpy.run_path(str(PLUGINS.parent / "scripts/ci-plugin-suites"))
    suites = ["plugins/ask-gate", "plugins/coach", "plugins/worktree-owner", "repo-guards"]
    selected, reasons = selector["select"](PLUGINS.parent, suites, ["plugins/kanban_inventory.py"])
    assert selected == suites
    assert "every suite" in reasons[0]


def test_stat_permission_error_is_not_missing(tmp_path, inventory, monkeypatch):
    path = db(tmp_path, "board")
    original = Path.stat

    def stat(self, *args, **kwargs):
        if self == path:
            raise PermissionError("unreadable board")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    with pytest.raises(PermissionError):
        inventory.board_db_files(tmp_path)


def test_fallback_is_query_only_and_closes_failed_ro(tmp_path, inventory, monkeypatch):
    path = db(tmp_path, "board")
    handles = []
    modes = []

    class Connection:
        def __init__(self, conn, ro):
            self.conn, self.ro, self.closed = conn, ro, False

        def execute(self, sql):
            if self.ro:
                raise sqlite3.OperationalError("missing WAL sidecar")
            if sql.startswith("SELECT"):
                assert self.conn.execute("PRAGMA query_only").fetchone() == (1,)
            return self.conn.execute(sql)

        def close(self):
            self.closed = True
            self.conn.close()

    def connect(uri, /, **kwargs):
        assert kwargs == {"uri": True}
        mode = uri.rsplit("?mode=", 1)[1]
        modes.append(mode)
        assert mode in ("ro", "rw")
        if mode == "rw":
            assert handles[0].closed
        handle = Connection(sqlite3.connect(uri, **kwargs), mode == "ro")
        handles.append(handle)
        return handle

    monkeypatch.setattr(inventory, "sqlite3", SimpleNamespace(
        connect=connect, OperationalError=sqlite3.OperationalError, Error=sqlite3.Error))
    assert inventory.board_db_files(tmp_path) == [path]
    assert modes == ["ro", "rw"]
    assert all(c.closed for c in handles)


def test_disappeared_board_is_not_recreated_by_fallback(tmp_path, inventory, monkeypatch):
    path = db(tmp_path, "board")
    modes = []

    def connect(uri, /, **kwargs):
        modes.append(uri.rsplit("?mode=", 1)[1])
        path.unlink(missing_ok=True)
        return sqlite3.connect(uri, **kwargs)

    monkeypatch.setattr(inventory, "sqlite3", SimpleNamespace(
        connect=connect, OperationalError=sqlite3.OperationalError, Error=sqlite3.Error))
    with pytest.raises(sqlite3.OperationalError):
        inventory.board_db_files(tmp_path)
    assert modes == ["ro", "rw"]
    assert not path.exists()


@pytest.mark.parametrize("relative,function", [
    ("follow-through/core.py", "kanban_dbs"),
    ("user-steps/cards.py", "boards"),
    ("user-steps/core.py", "kanban_boards"),
    ("coach/self_heal.py", "_board_dbs"),
])
def test_consumer_inventory_ignores_placeholders(tmp_path, relative, function):
    named = tmp_path / "kanban" / "boards"
    valid = db(named, "work")
    db(named, "zero", schema=None)
    db(named, "schema-less", schema="CREATE TABLE unrelated(id TEXT)")
    db(named, "_archived")
    module = load(relative)
    result = getattr(module, function)(tmp_path)
    paths = [item[1] if isinstance(item, tuple) else item for item in result]
    assert valid in paths
    assert not any(p.parent.name in {"zero", "schema-less", "_archived"} for p in paths)


def test_symlinked_plugin_resolves_shared_helper(tmp_path):
    link = tmp_path / "installed-plugins" / "follow-through"
    link.parent.mkdir()
    link.symlink_to(PLUGINS / "follow-through", target_is_directory=True)
    spec = importlib.util.spec_from_file_location("symlink_inventory_consumer", link / "core.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    home = tmp_path / "home"
    valid = db(home / "kanban" / "boards", "work")
    assert module.kanban_dbs(home) == [valid]


@pytest.mark.parametrize("function", ["owner_status", "owner_task"])
def test_root_cap_retains_unreadable_board_error_contract(tmp_path, function, monkeypatch):
    module = load("coach/root_cap.py")
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    path = db(tmp_path / "kanban" / "boards", "broken", schema=None)
    path.write_bytes(b"not SQLite" * 100)
    with pytest.raises(module.RootCapError, match="kanban board unreadable"):
        getattr(module, function)("t_example", tmp_path)

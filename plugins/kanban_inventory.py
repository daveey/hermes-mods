"""Conservative, non-creating inventory of direct named Kanban boards.

Plugin directories are symlinked independently. Consumers load this file from
``Path(__file__).resolve().parents[1]`` (parents[2] for dashboard modules),
without modifying sys.path or depending on a plugin package name.
"""
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import stat

_BOARD_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def _has_tasks(path: Path) -> bool:
    """Skip SQLite placeholders, but propagate real board read failures.

    mode=rw is a WAL-sidecar compatibility fallback, never mode=rwc: even
    deletion between the stat and open must not recreate a database. Always
    close the failed read-only handle before retrying and both successful ones.
    """
    uri = path.resolve().as_uri()
    try:
        with closing(sqlite3.connect(uri + "?mode=ro", uri=True)) as conn:
            return conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
            ).fetchone() is not None
    except sqlite3.OperationalError:
        with closing(sqlite3.connect(uri + "?mode=rw", uri=True)) as conn:
            conn.execute("PRAGMA query_only=ON")
            return conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
            ).fetchone() is not None


def board_db_files(directory, *, include_unreadable=False) -> list[Path]:
    """Sorted existing direct ``<slug>/kanban.db`` files with a tasks table.

    Missing roots and schema-less SQLite files are not boards. Archived and
    otherwise invalid directory names are ignored before opening their files.
    Unreadable/corrupt eligible files raise, so destructive callers fail closed.
    Recovery readers may explicitly retain their paths with include_unreadable;
    they must apply their existing per-board error policy when opening them.
    """
    directory = Path(directory)
    try:
        children = sorted(directory.iterdir())
    except FileNotFoundError:
        return []
    boards = []
    for child in children:
        if not _BOARD_SLUG.fullmatch(child.name):
            continue
        path = child / "kanban.db"
        try:
            if not stat.S_ISDIR(child.stat().st_mode):
                continue
            is_file = stat.S_ISREG(path.stat().st_mode)
        except FileNotFoundError:
            continue
        if is_file:
            try:
                if _has_tasks(path):
                    boards.append(path)
            except (OSError, sqlite3.Error) as exc:
                if include_unreadable:
                    boards.append(path)
                    continue
                setattr(exc, "board_db_path", path)
                raise
    return boards

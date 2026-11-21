"""Stand-in for core's `hermes kanban source-custody remove` in suites that use miniature boards.

The real CLI needs a full board schema and an installed core. This double keeps only the parts of its
contract the plugin relies on: it needs an owner id, it checks the id against the owner marker when one
exists, it never forces, and it answers "removed" (None) or a keep reason. `calls` records every request so
a test can assert what the plugin asked for and under which board state."""
import json
import os
import subprocess
from pathlib import Path

MARKER = "hermes-owner.json"


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=60)


def _marker_owner(rec: dict) -> str | None:
    if rec.get("tool") and rec.get("creation_id"):
        return f"created:{rec['tool']}/{rec['creation_id']}"
    if rec.get("task_id"):
        return f"task:{rec['task_id']}@{rec.get('run_id')}"
    if rec.get("agent") and rec.get("agent_session_id"):
        return f"session:{rec['agent']}/{rec['agent_session_id']}"
    return None


def install(module, before=None):
    """Replace module.custody_remove. *before(path, owner_id, db)* runs first, e.g. to probe the board."""
    calls = []

    def custody_remove(path, owner_id, db=None):
        calls.append(dict(path=os.path.realpath(path), owner=owner_id, db=db))
        if before:
            before(path, owner_id, db)
        if not owner_id:
            return "no owner evidence to remove it with"
        gitdir = _git(path, "rev-parse", "--absolute-git-dir")
        common = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if gitdir.returncode or common.returncode:
            return "source-custody remove exit 1: not a worktree"
        marker = Path(gitdir.stdout.strip(), MARKER)
        if marker.exists():
            try:
                owner = _marker_owner(json.loads(marker.read_text()))
            except (OSError, ValueError):
                return "source-custody remove exit 1: unreadable owner marker"
            if owner != owner_id:
                return f"source-custody remove exit 1: marker owner is {owner}"
        elif not owner_id.startswith("task:"):
            return "source-custody remove exit 1: no owner marker"
        done = _git(os.path.dirname(common.stdout.strip()), "worktree", "remove", os.path.realpath(path))
        if done.returncode:
            return f"source-custody remove exit 3: {done.stderr.strip()[:120]}"
        return None

    module.custody_remove = custody_remove
    module.custody_calls = calls
    return calls

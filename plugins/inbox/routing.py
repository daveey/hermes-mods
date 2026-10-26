"""Route social-posting Inbox cards to the `pr` Desktop project.

Desktop groups a session under the project whose folder owns the session's ``cwd``
(``hermes_cli.projects_db.project_for_path``; longest folder wins). Each gateway has its OWN
``projects.db`` (the Mac's `pr` is /Users/<you>/code/pr, the hub's is /home/ubuntu/code/pr, with
different ids), so the target path is always resolved from the posting host's own database and
never copied from another host.

Only the cwd chosen for a NEW card changes. Re-posts, running chats and Kanban worktrees are not
touched here.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Optional

PROJECT_SLUG = "pr"

# Card types / key prefixes owned by the social workflow (x-presence skill and Bluesky engagement).
_SOCIAL_TYPE = re.compile(r"^(x|bsky|bluesky|social)[-_]", re.I)
_SOCIAL_KEY = re.compile(r"^(x|bsky|bluesky|social)[-:_]", re.I)

# Free text needs BOTH a platform and a social action, so a coding "PR" / pull-request card or a
# card that merely mentions X as a variable never matches.
_PLATFORM = re.compile(
    r"\b(twitter|tweets?|bluesky|bsky|bsky\.app|x\.com|x\s+(?:post|posts|reply|replies|dm|dms|draft|drafts|"
    r"account|timeline|clip|thread|engagement)|on\s+x)\b", re.I)
_ACTION = re.compile(
    r"\b(post(?:ing|ed|s)?|repl(?:y|ies|ying)|dms?|direct\s+messages?|drafts?|tweet(?:s|ed|ing)?|"
    r"engag(?:e|ement|ing)|mentions?|quote[- ]?(?:tweet|post)|thread|publish(?:ing|ed)?|"
    r"approve|approval|schedule[ds]?)\b", re.I)
# Hard negatives: coding-review vocabulary where "PR" means pull request.
_CODING_PR = re.compile(r"\b(pull\s+requests?|merge\s+(?:the\s+)?pr|pr\s*#\d+|ci\b.*\bpr\b|\bpr\b.*\b(?:ci|review comments?|checks?)\b)",
                        re.I)


def is_social(*, key: Optional[str] = None, type: Optional[str] = None, title: str = "",
              body: str = "", poster: Optional[str] = None) -> bool:
    """True for social publishing / draft approval / engagement replies / social DM monitoring."""
    if type and _SOCIAL_TYPE.match(type):
        return True
    if key and _SOCIAL_KEY.match(key):
        return True
    head = f"{title}\n{poster or ''}"
    if _PLATFORM.search(head) and _ACTION.search(head):
        return not (_CODING_PR.search(head) and not _strong_platform(head))
    return False


def _strong_platform(text: str) -> bool:
    return bool(re.search(r"\b(twitter|tweets?|bluesky|bsky|x\.com)\b", text, re.I))


def _ro(path: Path) -> sqlite3.Connection:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return conn
    except sqlite3.Error:
        conn = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=5)
        conn.execute("PRAGMA query_only=ON")
        return conn


def project_path(home: Path, slug: str = PROJECT_SLUG) -> Optional[str]:
    """Primary folder of project *slug* in *home*'s own projects.db (None when absent/archived)."""
    home = Path(home)
    db = home / "projects.db"
    if not db.exists() and home.parent.name == "profiles":  # a named profile with no projects of its own
        db = home.parent.parent / "projects.db"
    if not db.exists():
        return None
    try:
        conn = _ro(db)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT id, primary_path FROM projects WHERE slug=? AND archived=0", (slug,)).fetchone()
        if not row:
            return None
        pid, primary = row
        folder = conn.execute(
            "SELECT path FROM project_folders WHERE project_id=? ORDER BY is_primary DESC, added_at LIMIT 1",
            (pid,)).fetchone()
        return (folder[0] if folder else None) or primary or None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def within(path: Optional[str], folder: Optional[str]) -> bool:
    if not path or not folder:
        return False
    p, f = os.path.normpath(path), os.path.normpath(folder)
    return p == f or p.startswith(f.rstrip("/") + "/")


def route_cwd(home: Path, *, explicit: Optional[str], inherited: Optional[str], social: bool) -> Optional[str]:
    """Pick the cwd for a new card.

    explicit (caller passed cwd) always wins. A social card with no explicit cwd goes to the host's
    `pr` project folder, except that an inherited cwd already inside that folder is kept (a worktree
    or the twitter/ subdir stays where it is). Anything else keeps the inherited cwd.
    """
    if explicit:
        return explicit
    if social:
        target = project_path(home)
        if target:
            return inherited if within(inherited, target) else target
    return inherited

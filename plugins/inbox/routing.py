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
# Classify the requested work, not its subject: coding tasks often mention social APIs.
# Conversely, a genuine draft can be ABOUT a pull request, so PR # alone is not a veto.
_CODING_TASK = re.compile(
    r"^\s*(?:please\s+)?(?:fix|debug|implement|refactor|merge|deploy|test|"
    r"review\s+(?:(?:the|a)\s+)?(?:pull\s+request|pr\b)|"
    r"(?:pull\s+request|pr\s*#?\d*)\s+review)\b", re.I)
_CODING_OBJECT = re.compile(r"\b(?:parser|api|scheduler|bug|code|unit\s+tests?)\b", re.I)
_PUBLISH_INTENT = re.compile(
    r"^\s*(?:please\s+)?(?:draft|write|publish|post|tweet|approve|schedule|reply)\b", re.I)


def is_social(*, key: Optional[str] = None, type: Optional[str] = None, title: str = "",
              body: str = "", poster: Optional[str] = None) -> bool:
    """True for social publishing / draft approval / engagement replies / social DM monitoring."""
    if type and _SOCIAL_TYPE.match(type):
        return True
    if key and _SOCIAL_KEY.match(key):
        return True
    # Workflow-owned keys/types above are explicit signals. Free text is conservative;
    # neither a platform name nor a social poster overrides a coding task in the title.
    if _CODING_TASK.search(title):
        return False
    if _CODING_OBJECT.search(title) and not _PUBLISH_INTENT.search(title):
        return False
    head = f"{title}\n{poster or ''}"
    return bool(_PLATFORM.search(head) and _ACTION.search(head))


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


def poster_slug(poster: Optional[str]) -> Optional[str]:
    """Project slug a poster name maps to ('self-heal' -> 'self-heal', 'Self Heal' -> 'self-heal')."""
    s = re.sub(r"[^a-z0-9]+", "-", str(poster or "").lower()).strip("-")
    return s or None


def route_cwd(home: Path, *, explicit: Optional[str], inherited: Optional[str], social: bool,
              poster: Optional[str] = None) -> Optional[str]:
    """Pick the cwd for a new card.

    explicit (caller passed cwd) always wins. A social card with no explicit cwd goes to the host's
    `pr` project folder; otherwise a card whose poster names a live project on this host (poster
    'self-heal' -> project slug 'self-heal') goes to that project's folder. In both cases an
    inherited cwd already inside the target folder is kept (a worktree or subdir stays where it is).
    Anything else keeps the inherited cwd.
    """
    if explicit:
        return explicit
    slug = poster_slug(poster)
    for target in ((project_path(home) if social else None),
                   (project_path(home, slug) if slug else None)):
        if target:
            return inherited if within(inherited, target) else target
    return inherited

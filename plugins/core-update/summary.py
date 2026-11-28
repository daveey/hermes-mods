"""Human-readable summary of pending core commits and affected sessions/cards.

Never exposes git hashes or raw conventional-commit prefixes to the UI. Joins
pending commits against local paused turns, Kanban wake checks, and card-to-chat
links so the user sees which chats resume or unblock, and upgrades the commit
digest asynchronously via ``hermes-pool-llm`` when available without blocking
``GET /version``.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

SUMMARY_SCHEMA = 1
MAX_BULLETS = 6
POOL_TIMEOUT_S = 15

_SHA_TOKEN_RE = re.compile(r"\b(?:(?=[0-9a-f]*\d)[0-9a-f]{7,40}|[0-9a-f]{12,40})\b")
_CARD_ID_RE = re.compile(r"\bt_[0-9a-z]{8,24}\b")
_PR_REF_RE = re.compile(r"(?:\bPR\s*#?\d+\b|\(#\d+\))", re.IGNORECASE)
_TAG_BRACKET_RE = re.compile(r"\s*\[[A-Za-z0-9_.-]+\]\s*")
_CONV_PREFIX_RE = re.compile(
    r"^(?:(?P<kind>feat|fix|perf|refactor|docs|test|tests|chore|build|ci|style|revert)"
    r"(?:\((?P<scope>[^)]+)\))?!?\s*:\s*)",
    re.IGNORECASE,
)
_INTERNAL_SUBJECT_RE = re.compile(
    r"^(?:test|tests|chore|ci|style)(?:\([^)]*\))?!?:\s*"
    r"|^cover\b.*\b(?:clauses|branches|cases)\b"
    r"|\b(?:test fixture|ratchet|lint gate|flake fixture)\b",
    re.IGNORECASE,
)
_SUBSYSTEM_WORDS = {
    "kanban": "task board",
    "session-state": "session status",
    "SessionState": "session status",
    "tui_gateway": "chat backend",
    "user-steps": "decision queue",
}
_INFLIGHT_KEYS: set[str] = set()
_INFLIGHT_LOCK = threading.Lock()


def _ro_connect(db_path: Path) -> Optional[sqlite3.Connection]:
    """Read-only SQLite connection safe on WAL DBs without sidecar files."""
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=rw", uri=True, timeout=2.0)
        conn.execute("PRAGMA query_only=ON")
        return conn
    except sqlite3.Error:
        return None


def _homes(root_home: Path) -> List[Path]:
    homes = [root_home]
    profiles = root_home / "profiles"
    try:
        if profiles.is_dir():
            homes.extend(sorted(p for p in profiles.iterdir() if p.is_dir() and not p.name.startswith(".")))
    except OSError:
        pass
    return homes


def _pid_alive(pid: Any) -> bool:
    try:
        n = int(pid)
        if n <= 0:
            return False
        os.kill(n, 0)
        return True
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False


def _session_titles(home: Path, session_ids: Sequence[str]) -> Dict[str, str]:
    ids = [s for s in dict.fromkeys(session_ids) if isinstance(s, str) and s]
    if not ids:
        return {}
    conn = _ro_connect(home / "state.db")
    if conn is None:
        return {}
    try:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id, COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(display_name), '')) "
            f"FROM sessions WHERE id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return {str(sid): _clean_label(title) for sid, title in rows if sid and title and _clean_label(title)}


def _clean_label(text: Any, limit: int = 72) -> str:
    s = " ".join(str(text or "").split()).strip()
    s = _TAG_BRACKET_RE.sub(" ", s).strip()
    if len(s) > limit:
        return s[: limit - 1].rstrip() + "…"
    return s


def _paused_sessions(root_home: Path, active_update: bool) -> List[str]:
    """Human titles of chats currently paused for or interrupted by this update."""
    titles: List[str] = []
    for home in _homes(root_home):
        marker_path = home / "desktop" / "interrupted_turns.json"
        try:
            entries = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(entries, dict):
            continue
        matched_ids: List[str] = []
        for sid, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            reason = str(entry.get("reason") or "").lower()
            if "update" in reason or "pause" in reason:
                matched_ids.append(sid)
            elif active_update and entry.get("auto_continue") is True and _pid_alive(entry.get("writer_pid")):
                matched_ids.append(sid)
        by_id = _session_titles(home, matched_ids)
        for sid in matched_ids:
            label = by_id.get(sid)
            if label and label not in titles:
                titles.append(label)
    return titles[:4]


def _kanban_dbs(root_home: Path) -> List[Tuple[Path, Path]]:
    """(home, kanban_db_path) pairs across root and profiles."""
    out: List[Tuple[Path, Path]] = []
    seen: set[Path] = set()
    for home in _homes(root_home):
        candidates = [home / "kanban.db"]
        boards_dir = home / "kanban" / "boards"
        try:
            if boards_dir.is_dir():
                candidates.extend(
                    sorted(p for p in boards_dir.glob("*/kanban.db") if p.parent.name != "_archived")
                )
        except OSError:
            pass
        for db in candidates:
            try:
                resolved = db.resolve()
            except OSError:
                continue
            if db.is_file() and resolved not in seen:
                seen.add(resolved)
                out.append((home, db))
    return out


def _linked_unblocks(root_home: Path, commits: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    """Match pending commits against parked Kanban wake checks and card->chat links."""
    if not commits:
        return []
    shas = [c["sha"] for c in commits if isinstance(c.get("sha"), str) and len(c["sha"]) >= 8]
    short_map = {sha[:8]: sha for sha in shas}
    short_map.update({sha[:10]: sha for sha in shas})
    short_map.update({sha: sha for sha in shas})
    mentioned_cards: Dict[str, str] = {}
    for c in commits:
        text = f"{c.get('subject', '')}\n{c.get('body', '')}"
        for match in _CARD_ID_RE.findall(text):
            mentioned_cards.setdefault(match, c["sha"])

    session_lookup: Dict[Path, Dict[str, str]] = {}
    impacts: List[Dict[str, str]] = []
    seen_targets: set[str] = set()

    for home, db_path in _kanban_dbs(root_home):
        conn = _ro_connect(db_path)
        if conn is None:
            continue
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "tasks" not in tables:
                continue
            matched_task_sha: Dict[str, str] = {}
            if "task_wake_checks" in tables:
                for task_id, cmd in conn.execute("SELECT task_id, cmd FROM task_wake_checks").fetchall():
                    if not task_id or not cmd:
                        continue
                    for token in _SHA_TOKEN_RE.findall(str(cmd)):
                        full = short_map.get(token) or next((s for s in shas if s.startswith(token)), None)
                        if full:
                            matched_task_sha[str(task_id)] = full
                            break
            for card_id, sha in mentioned_cards.items():
                matched_task_sha.setdefault(card_id, sha)
            if not matched_task_sha:
                continue

            ids = list(matched_task_sha)
            placeholders = ",".join("?" for _ in ids)
            task_rows = conn.execute(
                f"SELECT id, title, status FROM tasks WHERE id IN ({placeholders})",
                tuple(ids),
            ).fetchall()
            task_info = {str(tid): (str(title or ""), str(status or "")) for tid, title, status in task_rows}

            chat_links: Dict[str, List[Tuple[str, str]]] = {}
            if "task_chat_links" in tables:
                link_rows = conn.execute(
                    f"SELECT task_id, chat_profile, chat_session FROM task_chat_links "
                    f"WHERE released = 0 AND task_id IN ({placeholders})",
                    tuple(ids),
                ).fetchall()
                for tid, prof, sess in link_rows:
                    if tid and sess:
                        chat_links.setdefault(str(tid), []).append((str(prof or "default"), str(sess)))
        except sqlite3.Error:
            continue
        finally:
            conn.close()

        for tid, sha in matched_task_sha.items():
            title, status = task_info.get(tid, ("", ""))
            if status in ("archived",):
                continue
            session_title = ""
            for prof, sess_id in chat_links.get(tid, ()):
                prof_home = root_home if prof in ("", "default") else root_home / "profiles" / prof
                if prof_home not in session_lookup:
                    session_lookup[prof_home] = {}
                if sess_id not in session_lookup[prof_home]:
                    session_lookup[prof_home].update(_session_titles(prof_home, [sess_id]))
                if session_lookup[prof_home].get(sess_id):
                    session_title = session_lookup[prof_home][sess_id]
                    break
            clean_task = _sanitize_prose(title)
            target_key = session_title or clean_task
            if not target_key or target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            impacts.append({
                "sha": sha,
                "session": session_title,
                "task": clean_task,
                "status": status,
            })
            if len(impacts) >= 3:
                return impacts
    return impacts


def _sanitize_prose(text: str) -> str:
    """Remove git hashes, card IDs, PR tags and bracketed author tags from human copy."""
    s = _TAG_BRACKET_RE.sub(" ", str(text or ""))
    s = _CONV_PREFIX_RE.sub("", s)
    s = _CARD_ID_RE.sub("", s)
    s = _PR_REF_RE.sub("", s)
    s = _SHA_TOKEN_RE.sub("", s)
    for raw, human in _SUBSYSTEM_WORDS.items():
        s = re.sub(rf"\b{re.escape(raw)}\b", human, s)
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\s*[:;,-]\s*$", "", s)
    s = " ".join(s.split()).strip(" -–—·:;")
    return s


def humanize_commit(subject: str, body: str = "") -> Tuple[str, bool, str]:
    """Return (human_sentence, is_internal_only, topic_key) for one commit."""
    raw = str(subject or "").strip()
    internal = bool(_INTERNAL_SUBJECT_RE.search(raw))
    m = _CONV_PREFIX_RE.match(_TAG_BRACKET_RE.sub(" ", raw).strip())
    kind = (m.group("kind") or "").lower() if m else ""
    scope = (m.group("scope") or "").lower() if m else ""
    cleaned = _sanitize_prose(raw)
    if not cleaned:
        return ("", True, "empty")

    # Strip leading scope-like prefix such as "SessionState: ..." or "hub-status: ..."
    scope_lead = re.match(r"^([A-Za-z][A-Za-z0-9_-]{1,24}):\s+(.+)$", cleaned)
    if scope_lead:
        lead_word, rest = scope_lead.group(1), scope_lead.group(2)
        if not scope:
            scope = lead_word.lower()
        cleaned = rest

    lower = cleaned.lower()
    scope_human = _SUBSYSTEM_WORDS.get(scope, "")
    if lower.startswith("wake "):
        cleaned = "Wakes " + cleaned[5:]
        if scope_human and scope_human.lower() not in cleaned.lower():
            cleaned = f"{cleaned} ({scope_human})"
    elif lower.startswith("retain "):
        cleaned = "Keeps " + cleaned[7:]
    elif lower.startswith("preserve "):
        cleaned = "Preserves " + cleaned[9:]
    elif lower.startswith("recover "):
        cleaned = "Recovers " + cleaned[8:]
    elif kind == "fix" or lower.startswith(("fix ", "fixes ", "avoid ", "prevent ", "repair ")):
        if lower.startswith("fix "):
            cleaned = "Fixes " + cleaned[4:]
        elif lower.startswith("avoid "):
            cleaned = "Prevents " + cleaned[6:]
        elif lower.startswith("prevent "):
            cleaned = "Prevents " + cleaned[8:]
        elif lower.startswith("repair "):
            cleaned = "Fixes " + cleaned[7:]
        elif not lower.startswith("fixes "):
            cleaned = "Fixes " + cleaned[0].lower() + cleaned[1:]
    elif lower.startswith("make "):
        cleaned = "Makes " + cleaned[5:]
    elif lower.startswith("add "):
        cleaned = "Adds " + cleaned[4:]
    else:
        cleaned = cleaned[0].upper() + cleaned[1:]

    # Topic signature so 3 commits touching the same replan/worker/session-wake thread collapse cleanly.
    tokens = [
        w for w in re.findall(r"[a-z]{4,}", (scope + " " + lower))
        if w not in {"fixes", "fixed", "makes", "make", "adds", "keep", "keeps", "preserves", "preserve",
                     "when", "with", "from", "into", "across", "after", "before", "only", "that", "this"}
    ]
    if {"replan", "worker"} & set(tokens) and ({"replan", "recovery", "compact", "convergence"} & set(tokens)):
        topic = "worker-replan-recovery"
    elif {"session", "wake", "dependencies", "dependency", "replica"} & set(tokens) and (
        "dependenc" in lower or "wake" in lower
    ):
        topic = "session-dependency-wake"
    else:
        topic = "-".join(tokens[:3]) or cleaned[:24].lower()

    return (cleaned.rstrip("."), internal, topic)


def deterministic_bullets(
    commits: Sequence[Dict[str, str]],
    impacts: Sequence[Dict[str, str]],
    *,
    desktop_swap_pending: Optional[bool] = None,
) -> List[str]:
    """Fast, zero-network plain-English bullets for ``commits``."""
    if not commits:
        if desktop_swap_pending is True:
            return ["Finishes updating the Hermes Desktop app when it restarts"]
        return []

    by_sha: Dict[str, str] = {}
    for c in commits:
        bullet, _internal, _topic = humanize_commit(c.get("subject", ""), c.get("body", ""))
        if bullet:
            by_sha[c["sha"]] = bullet

    bullets: List[str] = []
    used_shas: set[str] = set()
    for imp in impacts:
        sha = imp.get("sha", "")
        reason = by_sha.get(sha, "")
        used_shas.add(sha)
        if imp.get("session"):
            tail = f" — {reason[0].lower() + reason[1:]}" if reason else ""
            bullets.append(f"Unblocks “{imp['session']}”{tail}")
        elif imp.get("task"):
            bullets.append(f"Unblocks task “{imp['task']}”")

    seen_topics: set[str] = set()
    internal_count = 0
    for c in commits:
        if c.get("sha") in used_shas:
            continue
        bullet, internal, topic = humanize_commit(c.get("subject", ""), c.get("body", ""))
        if not bullet:
            continue
        if internal:
            internal_count += 1
            continue
        if topic in seen_topics:
            continue
        seen_topics.add(topic)
        bullets.append(bullet)
        if len(bullets) >= MAX_BULLETS:
            break

    if not bullets and internal_count:
        n = len(commits)
        return [f"Internal reliability and test checks ({n} {'change' if n == 1 else 'changes'}); no visible workflow changes"]
    return bullets[:MAX_BULLETS]


def _sanitize_llm_bullets(raw_text: str) -> Optional[List[str]]:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    items = parsed.get("bullets") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        return None
    out: List[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        cleaned = _sanitize_prose(item.lstrip("•*- ").strip()).rstrip(".")
        if cleaned and cleaned not in out:
            out.append(cleaned[0].upper() + cleaned[1:])
        if len(out) >= MAX_BULLETS:
            break
    return out or None


def _pool_prompt(commits: Sequence[Dict[str, str]], impacts: Sequence[Dict[str, str]]) -> Tuple[str, str]:
    system = (
        "You write short, plain-English 'What's in the update' release bullets for a single human user of Hermes Desktop. "
        "Return ONLY JSON: {\"bullets\": [\"...\", ...]} with 2 to 5 concise bullets. "
        "Rules: "
        "1) Focus on what the user will notice or care about: bugs fixed, sessions/tasks unblocked, smoother behavior. "
        "2) Group related commits on the same topic into ONE bullet; omit pure unit-test/coverage commits unless every commit is internal. "
        "3) Only name a chat/session if it appears in Matched sessions/tasks below; never invent session names. "
        "4) NEVER include git commit hashes/SHAs, PR numbers, card IDs (t_...), author tags ([daveey]), conventional prefixes (fix(kanban):), file paths, or internal class/function names."
    )
    lines = ["Pending changes:"]
    for c in commits[:30]:
        subj = _TAG_BRACKET_RE.sub(" ", c.get("subject", "")).strip()
        body_first = " ".join((c.get("body") or "").split("\n\n", 1)[0].split())[:220]
        lines.append(f"- {subj}" + (f" — {body_first}" if body_first and not body_first.startswith("Proof:") else ""))
    if impacts:
        lines.append("\nMatched sessions/tasks affected by these changes:")
        for imp in impacts:
            if imp.get("session"):
                lines.append(f"- Unblocks session \"{imp['session']}\" (task: {imp.get('task') or 'linked work'})")
            elif imp.get("task"):
                lines.append(f"- Unblocks task \"{imp['task']}\"")
    return system, "\n".join(lines)


def _run_pool_summary(
    pool_bin: Path,
    cache_file: Path,
    lock_file: Path,
    commits: Sequence[Dict[str, str]],
    impacts: Sequence[Dict[str, str]],
    runner: Callable[..., Any] = subprocess.run,
) -> Optional[List[str]]:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        if cache_file.is_file():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8")).get("bullets")
                if isinstance(cached, list) and cached:
                    return cached
            except (OSError, ValueError):
                pass
        system, prompt = _pool_prompt(commits, impacts)
        for provider in ("anthropic", "openai-codex"):
            try:
                proc = runner(
                    [str(pool_bin), "ask", "--provider", provider, "--tier", "fast",
                     "--max-tokens", "450", "--system", system, "-"],
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=POOL_TIMEOUT_S,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if proc.returncode != 0 or not proc.stdout:
                continue
            bullets = _sanitize_llm_bullets(proc.stdout)
            if bullets:
                tmp = cache_file.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_text(
                    json.dumps({"schema": SUMMARY_SCHEMA, "provider": provider, "at": time.time(), "bullets": bullets}) + "\n",
                    encoding="utf-8",
                )
                os.replace(tmp, cache_file)
                return bullets
    return None


def build_summary(
    root_home: Path,
    state_dir: Path,
    *,
    running: Optional[str],
    target: Optional[str],
    commits: Sequence[Dict[str, str]],
    desktop_swap_pending: Optional[bool],
    active_update: bool,
    allow_async_llm: Optional[bool] = None,
) -> Dict[str, Any]:
    """Compute the human bulleted summary for ``running..target`` without blocking."""
    paused = _paused_sessions(root_home, active_update)
    resume_bullets: List[str] = []
    if paused and (commits or desktop_swap_pending or active_update):
        if len(paused) == 1:
            resume_bullets.append(f"Resumes “{paused[0]}” automatically once the update finishes")
        else:
            quoted = ", ".join(f"“{t}”" for t in paused[:2])
            more = f" +{len(paused) - 2} more" if len(paused) > 2 else ""
            resume_bullets.append(f"Resumes {len(paused)} paused chats ({quoted}{more}) once the update finishes")

    if not commits:
        base = deterministic_bullets([], [], desktop_swap_pending=desktop_swap_pending)
        return {
            "bullets": (resume_bullets + base)[:MAX_BULLETS],
            "source": "deterministic",
            "pending": False,
            "commit_count": 0,
        }

    impacts = _linked_unblocks(root_home, commits)
    key_payload = json.dumps(
        [SUMMARY_SCHEMA, running or "", target or "", [c.get("sha") for c in commits], impacts],
        sort_keys=True,
    )
    digest = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()[:20]
    summaries_dir = state_dir / "summaries"
    cache_file = summaries_dir / f"{digest}.json"
    lock_file = summaries_dir / f"{digest}.lock"

    try:
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        cached_bullets = cached.get("bullets")
        if isinstance(cached_bullets, list) and cached_bullets:
            merged = resume_bullets + [b for b in cached_bullets if b not in resume_bullets]
            return {
                "bullets": merged[:MAX_BULLETS],
                "source": "pool-llm",
                "pending": False,
                "commit_count": len(commits),
            }
    except (OSError, ValueError):
        pass

    pool_bin = root_home / "plugins" / "pool-llm" / "bin" / "hermes-pool-llm"
    if allow_async_llm is None:
        allow_async_llm = os.environ.get("HERMES_MODS_NO_POOL_SUMMARY") != "1" and os.access(pool_bin, os.X_OK)

    pending = False
    if allow_async_llm:
        with _INFLIGHT_LOCK:
            already = digest in _INFLIGHT_KEYS
            if not already:
                _INFLIGHT_KEYS.add(digest)
        if not already:
            def _bg():
                try:
                    _run_pool_summary(pool_bin, cache_file, lock_file, commits, impacts)
                except Exception:
                    pass
                finally:
                    with _INFLIGHT_LOCK:
                        _INFLIGHT_KEYS.discard(digest)

            threading.Thread(target=_bg, name=f"core-update-summary-{digest[:8]}", daemon=True).start()
        pending = True

    det = deterministic_bullets(commits, impacts, desktop_swap_pending=desktop_swap_pending)
    merged = resume_bullets + [b for b in det if b not in resume_bullets]
    return {
        "bullets": merged[:MAX_BULLETS],
        "source": "deterministic",
        "pending": pending,
        "commit_count": len(commits),
    }

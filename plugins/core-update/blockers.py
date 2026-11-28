"""Structured, human-readable update blockers and safe Stop / Skip actions.

Translates in-flight cron runs, active chat turns, local Kanban workers, batch
windows, and ``governor-other-defers.json`` strings into plain-English items
with safe per-item actions:
  - Cron runs: writes per-execution ``.cancel`` markers (the same contract as
    ``hermes cron cancel``), stopping only the live attempt while leaving the
    recurring schedule untouched.
  - Automated chat turns: records a CAS ``session_turn_end_requests`` entry
    against the live ``session_turn_leases`` holder.
  - Batch window / unforced waits: writes the standard ``force-request`` so
    the updater proceeds without waiting on the batch window.
"""
from __future__ import annotations

import contextlib
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

CRON_START_TOLERANCE = 200
CANCEL_REASON = "Stopped from Hub update panel"
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_EXEC_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_GATEWAY_UNITS_RE = re.compile(
    r"^gateway\s+(?P<home>\S+)\s+pid=\d+:\s+active=(?P<active>\d+)\s+units=(?P<units>\d+)\s+\((?P<kinds>[a-z_,]*)\)"
    r"(?:\s+sessions=(?P<sessions>\S+))?$"
)
_GATEWAY_UNIT_ITEM_RE = re.compile(
    r"^gateway\s+(?P<home>\S+)\s+pid=\d+:\s+(?P<kind>chat|delegation|api|cron|managed)\s+(?P<what>\S+)$"
)
_AUTO_HOLDER_RE = re.compile(r":auto=([a-z_]+)$")
_NOT_RUNNING_REASONS = frozenset({"paused_for_update", "update_paused", "queued"})


def _ro_connect(db_path: Path) -> Optional[sqlite3.Connection]:
    if not db_path.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=rw", uri=True, timeout=2.0)
        conn.execute("PRAGMA query_only=ON")
        return conn
    except sqlite3.Error:
        return None


def _homes(root_home: Path) -> List[Tuple[str, Path]]:
    out: List[Tuple[str, Path]] = [("default", root_home)]
    profiles = root_home / "profiles"
    try:
        if profiles.is_dir():
            for p in sorted(profiles.iterdir()):
                if p.is_dir() and not p.name.startswith(".") and _PROFILE_NAME_RE.match(p.name):
                    out.append((p.name, p))
    except OSError:
        pass
    return out


def _resolve_profile_home(root_home: Path, profile: Optional[str]) -> Path:
    prof = (profile or "default").strip()
    if prof in ("", "default", ".hermes", root_home.name):
        return root_home
    if not _PROFILE_NAME_RE.match(prof) or prof in {".", ".."}:
        raise ValueError(f"Invalid profile: {prof!r}")
    target = (root_home / "profiles" / prof).resolve()
    if target.parent != (root_home / "profiles").resolve():
        raise ValueError(f"Invalid profile path: {prof!r}")
    return target


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


def _process_start_fingerprint(pid: int) -> Optional[int]:
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().split()[21])
    except (OSError, IndexError, ValueError):
        pass
    try:
        import psutil  # type: ignore

        return round(psutil.Process(pid).create_time() * 100)
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=3,
            env={**os.environ, "LC_ALL": "C"},
        )
        text = r.stdout.strip()
        if r.returncode or not text:
            return None
        return round(time.mktime(time.strptime(text, "%a %b %d %H:%M:%S %Y")) * 100)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _cron_owner_live(pid: Any, started: Any) -> bool:
    try:
        n = int(pid)
    except (TypeError, ValueError):
        return True
    if n <= 0:
        return True
    if not _pid_alive(n):
        return False
    if started is None:
        return True
    cur = _process_start_fingerprint(n)
    if cur is None:
        return True
    try:
        return abs(int(cur) - int(started)) <= CRON_START_TOLERANCE
    except (TypeError, ValueError):
        return True


def _parse_iso_Or_epoch(value: Any, now: float) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, now - float(value)) if value > 1e8 else None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, now - float(text))
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        ts = dt.timestamp() if dt.tzinfo else time.mktime(dt.timetuple())
        return max(0.0, now - ts)
    except (ValueError, OverflowError, OSError):
        return None


def _fmt_elapsed(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m {s % 60}s" if m < 5 and s % 60 else f"{m}m"
    return f"{m // 60}h {m % 60}m"


def _cron_job_names(home: Path) -> Dict[str, str]:
    try:
        data = json.loads((home / "cron" / "jobs.json").read_text(encoding="utf-8"))
        jobs = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, list):
            return {}
        return {
            str(j["id"]): " ".join(str(j.get("name") or "").split()).strip()
            for j in jobs
            if isinstance(j, dict) and j.get("id") and j.get("name")
        }
    except (OSError, ValueError):
        return {}


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
    return {str(sid): " ".join(str(t).split()).strip() for sid, t in rows if sid and t}


def _holder_is_auto(holder: Any) -> bool:
    head, sep, _tail = str(holder or "").rpartition(":platform=")
    return bool(sep and _AUTO_HOLDER_RE.search(head))


def _live_cron_blockers(root_home: Path, now: float) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()
    for prof, home in _homes(root_home):
        conn = _ro_connect(home / "cron" / "executions.db")
        if conn is None:
            continue
        try:
            rows = conn.execute(
                "SELECT id, job_id, pid, process_started_at, COALESCE(started_at, claimed_at) "
                "FROM executions WHERE status IN ('claimed', 'running')"
            ).fetchall()
        except sqlite3.Error:
            continue
        finally:
            conn.close()
        names = _cron_job_names(home) if rows else {}
        for exec_id, job_id, pid, p_started, started_at in rows:
            if not job_id or not _cron_owner_live(pid, p_started):
                continue
            key_pair = (prof, str(job_id))
            if key_pair in seen:
                continue
            seen.add(key_pair)
            elapsed = _parse_iso_Or_epoch(started_at, now)
            dur = _fmt_elapsed(elapsed)
            label = names.get(str(job_id)) or "Scheduled background check"
            prof_note = f" ({prof})" if prof != "default" else ""
            items.append({
                "key": f"cron:{prof}:{job_id}",
                "kind": "cron",
                "id": str(job_id),
                "execution_id": str(exec_id) if exec_id else None,
                "profile": prof,
                "label": f"{label}{prof_note}",
                "detail": f"Scheduled job · running {dur}" if dur else "Scheduled job · running",
                "elapsed_s": round(elapsed, 1) if elapsed is not None else None,
                "action": "stop",
                "action_label": "Stop",
            })
    return items


def _live_turn_blockers(root_home: Path, now: float, *, forced: bool) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()
    for prof, home in _homes(root_home):
        leases: Dict[str, str] = {}
        conn = _ro_connect(home / "state.db")
        if conn is not None:
            try:
                rows = conn.execute(
                    "SELECT conversation_id, holder FROM session_turn_leases WHERE expires_at > ?",
                    (now,),
                ).fetchall()
                leases = {str(cid): str(holder or "") for cid, holder in rows if cid}
            except sqlite3.Error:
                leases = {}
            finally:
                conn.close()

        markers: Dict[str, Dict[str, Any]] = {}
        try:
            raw = json.loads((home / "desktop" / "interrupted_turns.json").read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for sid, entry in raw.items():
                    if isinstance(entry, dict) and entry.get("reason") not in _NOT_RUNNING_REASONS:
                        if _pid_alive(entry.get("writer_pid")):
                            markers[str(sid)] = entry
        except (OSError, ValueError):
            pass

        all_sids = list(dict.fromkeys([*markers.keys(), *leases.keys()]))
        if not all_sids:
            continue
        titles = _session_titles(home, all_sids)
        for sid in all_sids:
            if (prof, sid) in seen:
                continue
            seen.add((prof, sid))
            holder = leases.get(sid, "")
            is_auto = _holder_is_auto(holder)
            entry = markers.get(sid) or {}
            elapsed = _parse_iso_Or_epoch(entry.get("started_at"), now)
            dur = _fmt_elapsed(elapsed)
            title = titles.get(sid) or ("Background automated reply" if is_auto else "Active chat reply")
            prof_note = f" ({prof})" if prof != "default" else ""
            if is_auto:
                detail = f"Automated chat turn · running {dur}" if dur else "Automated chat turn"
                action, action_label = "stop", "Stop"
            elif forced:
                detail = f"Chat turn · pauses after current step ({dur})" if dur else "Chat turn · pauses after current step"
                action, action_label = None, None
            else:
                detail = f"Active chat · running {dur}" if dur else "Active chat · running"
                action, action_label = "force", "Pause & update"
            items.append({
                "key": f"turn:{prof}:{sid}",
                "kind": "turn",
                "id": sid,
                "holder": holder or None,
                "profile": prof,
                "label": f"{title}{prof_note}",
                "detail": detail,
                "elapsed_s": round(elapsed, 1) if elapsed is not None else None,
                "action": action,
                "action_label": action_label,
            })
    return items


def _live_kanban_blockers(root_home: Path, now: float, *, forced: bool) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for prof, home in _homes(root_home):
        dbs = [home / "kanban.db"]
        boards = home / "kanban" / "boards"
        if boards.is_dir():
            with contextlib.suppress(OSError):
                dbs.extend(sorted(p for p in boards.glob("*/kanban.db") if p.parent.name != "_archived"))
        for db_path in dbs:
            conn = _ro_connect(db_path)
            if conn is None:
                continue
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
                if "id" not in cols or "status" not in cols:
                    continue
                lock_col = "claim_lock" if "claim_lock" in cols else "NULL"
                holder_col = "lease_holder" if "lease_holder" in cols else "NULL"
                started_col = "worker_started_at" if "worker_started_at" in cols else "started_at"
                rows = conn.execute(
                    f"SELECT id, title, worker_pid, {lock_col}, {holder_col}, {started_col} "
                    "FROM tasks WHERE status = 'running'"
                ).fetchall()
            except sqlite3.Error:
                continue
            finally:
                conn.close()
            for tid, title, pid, claim_lock, lease_holder, started_at in rows:
                if lease_holder and not claim_lock:
                    continue
                if pid and not _pid_alive(pid):
                    continue
                if str(tid) in seen:
                    continue
                seen.add(str(tid))
                elapsed = _parse_iso_Or_epoch(started_at, now)
                dur = _fmt_elapsed(elapsed)
                clean_title = " ".join(str(title or "Background task card").split()).strip()
                items.append({
                    "key": f"kanban:{prof}:{tid}",
                    "kind": "kanban",
                    "id": str(tid),
                    "profile": prof,
                    "label": clean_title,
                    "detail": (
                        f"Card worker · running {dur}" if dur and not forced
                        else "Card worker · checkpointing for update" if forced
                        else "Card worker · running"
                    ),
                    "elapsed_s": round(elapsed, 1) if elapsed is not None else None,
                    "action": None if forced else "force",
                    "action_label": None if forced else "Skip wait",
                })
    return items


def _governor_reason(root_home: Path) -> str:
    for _prof, home in _homes(root_home):
        state_file = home / "bb-dev" / "governor-state.json"
        try:
            host = json.loads(state_file.read_text(encoding="utf-8")).get("host") or {}
            reasons = [str(r).strip() for r in (host.get("reasons") or []) if str(r).strip()]
            if reasons:
                return f"Usage governor hold ({reasons[0]})"
        except (OSError, ValueError, AttributeError):
            continue
    return "Paused by usage governor"


def _parse_defer_reason(
    raw: str,
    root_home: Path,
    *,
    existing_keys: set[str],
    has_cron_or_kanban: bool,
    forced: bool,
) -> Optional[Dict[str, Any]]:
    text = str(raw or "").strip()
    if not text:
        return None

    if text.startswith("cron:"):
        rest = text[len("cron:") :]
        if ":" in rest:
            prof, job_id = rest.split(":", 1)
        else:
            prof, job_id = "default", rest
        job_id = job_id.split(":", 1)[0].strip()
        key = f"cron:{prof}:{job_id}"
        if key in existing_keys:
            return None
        try:
            home = _resolve_profile_home(root_home, prof)
            label = _cron_job_names(home).get(job_id) or "Scheduled background check"
        except ValueError:
            label = "Scheduled background check"
        return {
            "key": key,
            "kind": "cron",
            "id": job_id,
            "execution_id": None,
            "profile": prof,
            "label": label if prof == "default" else f"{label} ({prof})",
            "detail": "Scheduled job · running",
            "elapsed_s": None,
            "action": "stop",
            "action_label": "Stop",
        }

    m_gw = _GATEWAY_UNITS_RE.match(text)
    if m_gw:
        kinds = {k for k in (m_gw.group("kinds") or "").split(",") if k}
        sessions_csv = m_gw.group("sessions") or ""
        if sessions_csv:
            home_name = m_gw.group("home")
            prof = "default" if home_name in (".hermes", root_home.name) else home_name
            sid = sessions_csv.split(",", 1)[0]
            key = f"turn:{prof}:{sid}"
            if key in existing_keys:
                return None
            try:
                home = _resolve_profile_home(root_home, prof)
                title = _session_titles(home, [sid]).get(sid) or "Active gateway chat"
            except ValueError:
                title = "Active gateway chat"
            return {
                "key": key,
                "kind": "turn",
                "id": sid,
                "profile": prof,
                "label": title,
                "detail": "Gateway chat · finishing reply",
                "elapsed_s": None,
                "action": None,
                "action_label": None,
            }
        # Suppress redundant daemon census summary when individual cron/kanban items are already listed.
        if has_cron_or_kanban and kinds and kinds <= {"cron", "managed"}:
            return None
        units = int(m_gw.group("units") or m_gw.group("active") or 1)
        return {
            "key": f"gateway:{m_gw.group('home')}",
            "kind": "gateway",
            "id": m_gw.group("home"),
            "profile": "default",
            "label": f"Background gateway finishing {units} active {'task' if units == 1 else 'tasks'}",
            "detail": "Waits for in-flight step to finish",
            "elapsed_s": None,
            "action": None,
            "action_label": None,
        }

    m_unit = _GATEWAY_UNIT_ITEM_RE.match(text)
    if m_unit:
        kind = m_unit.group("kind")
        what = m_unit.group("what")
        home_name = m_unit.group("home")
        prof = "default" if home_name in (".hermes", root_home.name) else home_name
        if kind == "chat":
            key = f"turn:{prof}:{what}"
            if key in existing_keys:
                return None
            try:
                title = _session_titles(_resolve_profile_home(root_home, prof), [what]).get(what) or "Active gateway chat"
            except ValueError:
                title = "Active gateway chat"
            return {
                "key": key,
                "kind": "turn",
                "id": what,
                "profile": prof,
                "label": title,
                "detail": "Gateway chat · finishing reply",
                "elapsed_s": None,
                "action": None,
                "action_label": None,
            }
        return {
            "key": f"gateway-unit:{prof}:{kind}:{what}",
            "kind": "gateway",
            "id": what,
            "profile": prof,
            "label": f"Background {kind} finishing",
            "detail": None,
            "elapsed_s": None,
            "action": None,
            "action_label": None,
        }

    if text.startswith("kanban "):
        rest = text[len("kanban ") :].strip()
        board_part, _, tid = rest.rpartition(":")
        prof = board_part.split(":", 1)[0] if ":" in board_part else "default"
        key = f"kanban:{prof}:{tid}"
        if key in existing_keys:
            return None
        return {
            "key": key,
            "kind": "kanban",
            "id": tid or rest,
            "profile": prof,
            "label": "Background task card finishing",
            "detail": None if forced else "Card worker · running",
            "elapsed_s": None,
            "action": None if forced else "force",
            "action_label": None if forced else "Skip wait",
        }

    if text.startswith("hold "):
        hold_name = text[len("hold ") :].strip()
        if "governor" in hold_name:
            label = _governor_reason(root_home)
        elif "core-wt-land" in hold_name:
            label = "Waiting for an in-progress branch merge to finish"
        else:
            label = f"Paused by {hold_name} hold"
        return {
            "key": f"hold:{hold_name}",
            "kind": "hold",
            "id": hold_name,
            "profile": "default",
            "label": label,
            "detail": None,
            "elapsed_s": None,
            "action": None if forced else "force",
            "action_label": None if forced else "Skip wait",
        }

    # Check "<profile>:<session_id>" desktop turn string
    if ":" in text and not text.startswith(("gateway ", "unreadable ", "invalid ", "unpublished ")):
        prof, sid = text.split(":", 1)
        if _PROFILE_NAME_RE.match(prof) and sid and " " not in sid:
            key = f"turn:{prof}:{sid}"
            if key in existing_keys:
                return None
            try:
                title = _session_titles(_resolve_profile_home(root_home, prof), [sid]).get(sid)
            except ValueError:
                title = None
            if title:
                return {
                    "key": key,
                    "kind": "turn",
                    "id": sid,
                    "profile": prof,
                    "label": title,
                    "detail": "Chat turn · finishing step" if forced else "Active chat · running",
                    "elapsed_s": None,
                    "action": None if forced else "force",
                    "action_label": None if forced else "Pause & update",
                }

    clean = re.sub(r"\b(?:(?=[0-9a-f]*\d)[0-9a-f]{8,40}|[0-9a-f]{12,40})\b", "", text).strip(" ;,-")
    clean = " ".join(clean.split()) or text
    return {
        "key": f"reason:{clean[:64]}",
        "kind": "reason",
        "id": clean[:64],
        "profile": "default",
        "label": clean[0].upper() + clean[1:] if clean else text,
        "detail": None,
        "elapsed_s": None,
        "action": None,
        "action_label": None,
    }


def collect_blockers(
    root_home: Path,
    *,
    progress: Optional[Dict[str, Any]],
    force_request: Optional[Dict[str, Any]],
    status: Dict[str, Any],
    waiter: Optional[Dict[str, Any]],
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Return human-readable blockers whenever an update is waiting or blocked."""
    now = time.time() if now is None else now
    p_state = (progress or {}).get("state")
    w_state = (waiter or {}).get("state") if waiter else None
    w_alive = bool(waiter and _pid_alive(waiter.get("pid")))
    forced = force_request is not None

    if p_state == "applying":
        return []
    waiting_active = (
        p_state in ("batching", "draining", "blocked")
        or forced
        or (w_alive and w_state in ("waiting", "draining", "deferred"))
    )
    if not waiting_active:
        return []

    items: List[Dict[str, Any]] = []
    if p_state == "batching" and not forced:
        items.append({
            "key": "window:batch",
            "kind": "window",
            "id": "batch",
            "profile": "default",
            "label": "Scheduled quiet update window",
            "detail": "Batching updates to avoid interrupting your work",
            "elapsed_s": None,
            "action": "force",
            "action_label": "Skip wait",
        })

    if status.get("state") == "building":
        items.append({
            "key": "build:desktop",
            "kind": "build",
            "id": "desktop",
            "profile": "default",
            "label": "Building Desktop app update",
            "detail": "Compiling and signing in the background",
            "elapsed_s": None,
            "action": None,
            "action_label": None,
        })
    elif w_alive and w_state == "waiting" and status.get("state") == "ready" and status.get("can_restart"):
        items.append({
            "key": "desktop:quit",
            "kind": "desktop",
            "id": "quit",
            "profile": "default",
            "label": "Waiting for Hermes Desktop to restart",
            "detail": "Ready to swap in the new app build",
            "elapsed_s": None,
            "action": "restart",
            "action_label": "Restart now",
        })

    items.extend(_live_cron_blockers(root_home, now))
    items.extend(_live_turn_blockers(root_home, now, forced=forced))
    items.extend(_live_kanban_blockers(root_home, now, forced=forced))

    existing_keys = {item["key"] for item in items}
    has_cron_or_kanban = any(item["kind"] in ("cron", "kanban") for item in items)

    raw_reasons: List[str] = []
    for entry in (progress or {}).get("waiting_on") or []:
        for part in str(entry).split(";"):
            if part.strip():
                raw_reasons.append(part.strip())
    if not progress and w_alive and w_state == "deferred" and waiter and waiter.get("reason"):
        raw_reasons.append(str(waiter["reason"]))

    for raw in raw_reasons:
        parsed = _parse_defer_reason(
            raw,
            root_home,
            existing_keys=existing_keys,
            has_cron_or_kanban=has_cron_or_kanban,
            forced=forced,
        )
        if parsed and parsed["key"] not in existing_keys:
            existing_keys.add(parsed["key"])
            items.append(parsed)
            if parsed["kind"] in ("cron", "kanban"):
                has_cron_or_kanban = True

    return items[:10]


def summarize_blockers_text(blockers: Sequence[Dict[str, Any]]) -> str:
    """Short one-line human fallback summary of ``blockers`` (never raw IDs)."""
    if not blockers:
        return ""
    labels = [str(b.get("label") or "").strip() for b in blockers if b.get("label")]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return f"{labels[0]} +{len(labels) - 1} more"


def _write_cancel_marker(home: Path, execution_id: str, reason: str = CANCEL_REASON) -> bool:
    exec_id = str(execution_id or "").strip()
    if not _EXEC_ID_RE.match(exec_id) or exec_id in {".", ".."}:
        raise ValueError(f"Invalid execution id: {exec_id!r}")
    cancels_dir = home / "cron" / "cancels"
    cancels_dir.mkdir(parents=True, exist_ok=True)
    target = cancels_dir / f"{exec_id}.cancel"
    if target.exists():
        return True
    body = reason.encode("utf-8")
    fd, tmp_str = tempfile.mkstemp(dir=cancels_dir, prefix=f"{target.name}.", suffix=".tmp")
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        with contextlib.suppress(FileExistsError):
            os.link(tmp, target)
            return True
    except OSError:
        pass
    finally:
        tmp.unlink(missing_ok=True)
    with contextlib.suppress(FileExistsError):
        with open(target, "xb") as handle:
            handle.write(body)
    return target.exists()


def _cancel_cron_job_in_home(home: Path, job_id: str, execution_id: Optional[str] = None) -> int:
    """Write ``.cancel`` markers for all live executions of ``job_id`` in ``home``."""
    count = 0
    exec_ids: List[str] = []
    if execution_id:
        exec_ids.append(execution_id)
    conn = _ro_connect(home / "cron" / "executions.db")
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT id, pid, process_started_at FROM executions "
                "WHERE job_id = ? AND status IN ('claimed', 'running')",
                (job_id,),
            ).fetchall()
            for eid, pid, p_started in rows:
                if eid and _cron_owner_live(pid, p_started) and str(eid) not in exec_ids:
                    exec_ids.append(str(eid))
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    for eid in exec_ids:
        if _write_cancel_marker(home, eid):
            count += 1
    return count


def _stop_auto_turn_in_home(home: Path, session_id: str, holder: Optional[str] = None) -> bool:
    db_path = home / "state.db"
    if not db_path.is_file() or not session_id:
        return False
    if not holder:
        conn = _ro_connect(db_path)
        if conn is not None:
            try:
                row = conn.execute(
                    "SELECT holder FROM session_turn_leases WHERE conversation_id = ? AND expires_at > ?",
                    (session_id, time.time()),
                ).fetchone()
                if row:
                    holder = str(row[0] or "")
            except sqlite3.Error:
                holder = None
            finally:
                conn.close()
    if not holder or not _holder_is_auto(holder):
        return False
    try:
        from hermes_state import SessionDB  # type: ignore

        if SessionDB(db_path=db_path).request_turn_end(session_id, holder, reason=CANCEL_REASON):
            return True
    except Exception:
        pass
    try:
        conn = sqlite3.connect(str(db_path.resolve()), timeout=3.0)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS session_turn_end_requests "
                "(conversation_id TEXT PRIMARY KEY, holder TEXT, requested_at REAL, reason TEXT)"
            )
            cur = conn.execute(
                "INSERT OR REPLACE INTO session_turn_end_requests (conversation_id, holder, requested_at, reason) "
                "SELECT conversation_id, holder, ?, ? FROM session_turn_leases "
                "WHERE conversation_id = ? AND holder = ?",
                (time.time(), CANCEL_REASON, session_id, holder),
            )
            conn.commit()
            return bool(cur.rowcount and cur.rowcount > 0)
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def act_on_blocker(root_home: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a safe Stop / Stop-all action requested by the Hub update panel."""
    if not isinstance(payload, dict):
        raise ValueError("Invalid blocker payload")
    action = str(payload.get("action") or "").strip()
    if action == "stop":
        kind = str(payload.get("kind") or "").strip()
        item_id = str(payload.get("id") or "").strip()
        if not item_id:
            raise ValueError("Missing blocker id")
        home = _resolve_profile_home(root_home, payload.get("profile"))
        if kind == "cron":
            stopped = _cancel_cron_job_in_home(home, item_id, payload.get("execution_id"))
            return {"ok": True, "stopped": stopped, "kind": "cron", "id": item_id}
        if kind == "turn":
            stopped = 1 if _stop_auto_turn_in_home(home, item_id, payload.get("holder")) else 0
            return {"ok": True, "stopped": stopped, "kind": "turn", "id": item_id}
        raise ValueError(f"Unsupported stop target kind: {kind!r}")

    if action == "stop_all":
        now = time.time()
        stopped = 0
        for item in _live_cron_blockers(root_home, now):
            home = _resolve_profile_home(root_home, item.get("profile"))
            stopped += _cancel_cron_job_in_home(home, item["id"], item.get("execution_id"))
        for item in _live_turn_blockers(root_home, now, forced=True):
            if item.get("action") == "stop":
                home = _resolve_profile_home(root_home, item.get("profile"))
                if _stop_auto_turn_in_home(home, item["id"], item.get("holder")):
                    stopped += 1
        return {"ok": True, "stopped": stopped}

    raise ValueError(f"Unsupported blocker action: {action!r}")

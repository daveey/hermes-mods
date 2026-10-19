"""blocked-panel backend: turn a blocked Kanban card's note into numbered steps for the human.

Workers often block with a status report ("Code is on main… the watch still reports build 295…")
instead of instructions. `steps_for()` asks the auxiliary model (task "blocked_steps") to rewrite
the block reason (+ card title, body and latest comments for context) into 2–7 imperative steps
that end with how to hand the card back. Results are cached on disk by input hash, so each
distinct reason costs one model call. The LLM callable is injected so tests can fake it; any
failure returns {"steps": [], "error": ...} and the panel falls back to its own parsing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

MAX_STEPS = 7
CACHE_MAX = 500
PROMPT_VERSION = 3

SYSTEM = """You write the "what you need to do" checklist for a person whose AI agent is blocked waiting on them.
Input: a Kanban card's title, the agent's block note, the card description and its latest comments.

Return ONLY JSON: {"steps": ["...", "..."]}

Rules:
- 2 to 7 steps, in the order the person does them. Each step is ONE concrete action the PERSON performs,
  imperative voice, starting with a verb ("Open…", "Plug in…", "Run…", "Reply with…").
- Say exactly where: the device, app, page, setting, menu path, file or command. Copy exact names,
  values, build numbers, times, URLs, file paths and comment numbers from the input. Never invent details
  that are not in the input; if the note points elsewhere for details (e.g. "steps in comment 1618"),
  use that comment's content when it is given, otherwise make the step "Open the card and read comment 1618".
- Only the PERSON's actions are steps. Work the agent, a worker or a parent session will do itself
  (unlinking cards, re-running checks, grading logs) is not a step.
- Leave out what the agent already did, progress reports and test results, unless the person must know it
  to act (then fold it into the step briefly).
- Time constraints go in the step they apply to ("After 07:00 PT, …").
- When the agent needs an answer or decision, the step says what to reply ("Reply with which option: A or B").
- The LAST step hands the card back: "Click Done → unblock" when the person only has to act, or
  "Click Reply & unblock and send <what>" when the agent needs information back.
- Keep each step under 35 words. No markdown, no numbering inside the strings.
- If the note says no human action is needed, return {"steps": []}."""


def _home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _cache_path(home: Optional[Path] = None) -> Path:
    return (home or _home()) / "cache" / "blocked-panel" / "steps.json"


_lock = threading.Lock()
_inflight: dict[str, threading.Event] = {}


def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def cache_key(title: str, reason: str, body: str, comments: list[str]) -> str:
    raw = json.dumps([PROMPT_VERSION, title, reason, body, comments], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(path: Path, data: dict) -> None:
    if len(data) > CACHE_MAX:
        keep = sorted(data.items(), key=lambda kv: kv[1].get("at", 0))[-CACHE_MAX:]
        data = dict(keep)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    os.replace(tmp, path)


_NUM = re.compile(r"^\s*(?:\d{1,2}[).:]|[-*•])\s+")


def parse_steps(text: str) -> list[str]:
    """Model output → clean step strings. Accepts JSON {"steps": [...]} (optionally fenced) or a list."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.S)
    steps = None
    if m:
        try:
            obj = json.loads(m.group(0))
            steps = obj.get("steps") if isinstance(obj, dict) else None
        except Exception:
            steps = None
    if steps is None:
        lines = [ln for ln in t.splitlines() if _NUM.match(ln)]
        steps = [_NUM.sub("", ln) for ln in lines]
    out = []
    for s in steps if isinstance(steps, list) else []:
        s = _NUM.sub("", str(s)).strip().strip('"').strip()
        if s:
            out.append(_clip(s, 400))
    return out[:MAX_STEPS]


def build_messages(title: str, reason: str, body: str, comments: list[str]) -> list[dict]:
    parts = [f"CARD TITLE: {_clip(title, 300)}", f"BLOCK NOTE:\n{_clip(reason, 4000) or '(none)'}"]
    if body:
        parts.append(f"CARD DESCRIPTION:\n{_clip(body, 3000)}")
    for i, c in enumerate(comments[-3:], 1):
        parts.append(f"COMMENT {i}:\n{_clip(c, 2000)}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "\n\n".join(parts)}]


def steps_for(
    title: str,
    reason: str,
    body: str = "",
    comments: Optional[list[str]] = None,
    llm: Optional[Callable[[list, int], str]] = None,
    home: Optional[Path] = None,
    cached_only: bool = False,
) -> dict:
    """{steps, cached} | {steps: [], error}. One model call per distinct input, shared across callers."""
    comments = [str(c) for c in (comments or []) if str(c).strip()][-3:]
    reason, body, title = (reason or "").strip(), (body or "").strip(), (title or "").strip()
    if not reason and not body:
        return {"steps": [], "error": "empty"}
    key = cache_key(title, reason, body, comments)
    path = _cache_path(home)
    with _lock:
        hit = _load(path).get(key)
        if hit:
            return {"steps": hit.get("steps", []), "cached": True}
        if cached_only:
            return {"steps": [], "pending": True}
        ev = _inflight.get(key)
        owner = ev is None
        if owner:
            ev = _inflight[key] = threading.Event()
    if not owner:
        ev.wait(60)
        hit = _load(path).get(key)
        return {"steps": hit.get("steps", []), "cached": True} if hit else {"steps": [], "error": "busy"}
    try:
        text = (llm or aux_llm)(build_messages(title, reason, body, comments), 700)
        steps = parse_steps(text)
        with _lock:
            data = _load(path)
            data[key] = {"steps": steps, "at": time.time()}
            _save(path, data)
        return {"steps": steps, "cached": False}
    except Exception as exc:  # fail open: the panel keeps its own parse
        return {"steps": [], "error": f"{type(exc).__name__}: {exc}"[:200]}
    finally:
        with _lock:
            _inflight.pop(key, None)
        ev.set()


def aux_llm(messages: list, max_tokens: int) -> str:
    from agent.auxiliary_client import call_llm  # type: ignore

    resp = call_llm(task="blocked_steps", messages=messages, max_tokens=max_tokens, temperature=0, timeout=45)
    return (getattr(resp.choices[0].message, "content", None) or "").strip()

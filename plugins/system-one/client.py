"""system-one client: fast typed judgments from a System One model (TypeSafe Jev) for other plugins.

    sys1 = load()                       # from a sibling plugin; None when this plugin isn't installed
    ans = sys1.decide("coach_fixloop", state, {"q": sys1.noul("Is ...?", true="...", false="...")})
    ans -> {"q": {"type": "noul", "noul": 0.93, ...}} or None

Contract: every failure (no key, mode off, timeout, HTTP/parse error) returns None, and the caller keeps its existing
LLM path. Stdlib only. One JSON line per call goes to <HERMES_HOME>/system-one/log.jsonl: decision name, mode,
latency, served/failed, numeric answers. Never the state or the key.

Config (env, read per call):
  TYPESAFE_API_KEY        key (env first, then <HERMES_HOME>/.env, so running backends see a key added later)
  SYSTEM_ONE_MODEL        default jev-1.14.0
  SYSTEM_ONE_TIMEOUT      seconds, default 1.5 (p95 measured 0.26 s; this is the deadline, not the budget)
  SYSTEM_ONE_MODE         on | shadow | off, default per decision (callers pass default=); a per-decision override is
                          SYSTEM_ONE_MODE_<NAME> (e.g. SYSTEM_ONE_MODE_TOPIC_SHIFT=on)
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-1.14.0"
MODES = ("on", "shadow", "off")
_lock = threading.Lock()


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # type: ignore
        return Path(get_hermes_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))


def api_key() -> Optional[str]:
    k = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if k:
        return k
    home = hermes_home()
    envs = [home / ".env"]
    if home.parent.name == "profiles":  # a profile without its own copy uses the root home's key
        envs.append(home.parent.parent / ".env")
    for env in envs:
        try:
            for line in env.read_text().splitlines():
                if line.startswith("TYPESAFE_API_KEY="):
                    v = line.split("=", 1)[1].strip().strip("'\"")
                    if v:
                        return v
        except OSError:
            pass
    return None


def mode(name: str, default: str = "shadow") -> str:
    m = os.environ.get(f"SYSTEM_ONE_MODE_{name.upper().replace('-', '_')}") or os.environ.get("SYSTEM_ONE_MODE") or default
    m = m.strip().lower()
    return m if m in MODES else default


def noul(instructions: str, true: str = "", false: str = "") -> dict:
    q: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {k: v for k, v in (("true", true), ("false", false)) if v}
    return q


def score(instructions: str, levels: list[str]) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": list(levels)}


def choice(instructions: str, options: dict[str, str]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


def log_path() -> Path:
    o = os.environ.get("SYSTEM_ONE_LOG")
    return Path(o) if o else hermes_home() / "system-one" / "log.jsonl"


def log(rec: dict) -> None:
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock, open(p, "a") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), **rec}) + "\n")
    except Exception:
        pass


def _numbers(answers: dict) -> dict:
    out = {}
    for k, a in (answers or {}).items():
        if not isinstance(a, dict):
            continue
        for f in ("noul", "score", "choice"):
            if f in a:
                out[k] = a[f]
                break
    return out


def _post(body: bytes, key: str, timeout: float) -> dict:
    req = urllib.request.Request(URL, data=body, headers={"Authorization": f"Bearer {key}",
                                                          "Content-Type": "application/json",
                                                          "User-Agent": "hermes-system-one/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def decide(name: str, state: Any, questions: dict, *, timeout: Optional[float] = None,
           transport=None) -> Optional[dict]:
    """-> answers dict keyed like `questions`, or None on any failure. `transport(body, key, timeout)` for tests."""
    key = api_key()
    if not key:
        log({"name": name, "ok": False, "why": "no_key"})
        return None
    model = os.environ.get("SYSTEM_ONE_MODEL", DEFAULT_MODEL)
    try:
        timeout = float(timeout if timeout is not None else os.environ.get("SYSTEM_ONE_TIMEOUT", "1.5"))
    except ValueError:
        timeout = 1.5
    body = json.dumps({"model": model, "state": state, "questions": questions}).encode()
    t0 = time.monotonic()
    try:
        d = (transport or _post)(body, key, timeout)
        answers = d.get("answers")
        if not isinstance(answers, dict) or not all(q in answers for q in questions):
            raise ValueError("missing answers")
    except Exception as exc:
        log({"name": name, "ok": False, "ms": round(1000 * (time.monotonic() - t0)), "why": type(exc).__name__})
        return None
    log({"name": name, "ok": True, "model": d.get("model") or model, "ms": round(1000 * (time.monotonic() - t0)),
         "tokens": (d.get("usage") or {}).get("input_tokens"), "answers": _numbers(answers)})
    return answers


def p(answers: Optional[dict], q: str) -> Optional[float]:
    """Noul probability (or score expectation) for question q, or None."""
    try:
        a = answers[q]  # type: ignore[index]
        return float(a["noul"] if "noul" in a else a["score"])
    except Exception:
        return None

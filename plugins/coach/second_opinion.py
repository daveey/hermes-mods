"""second_opinion: the coach's two checkpoints act instead of only advising.

1. Fix loop (pre_llm_call): on the 2nd judge-confirmed failed fix inside fix_loop's window, open ONE read-only
   review card pinned to a strong model (default claude-opus-5-5). It carries the user's complaints, the agent's
   prior claims, the files touched in the failing turns, and "trace and report what you observe; do not edit".
   The agent's hint names the card and says to read its handoff before patching again. Idempotent per
   session + loop window (kanban idempotency key; a 3rd failure in the same window reuses the card).
2. Reply check (pre_verify): when the judge flags `unverified` AND this turn changed a git repo (changed_paths,
   patch/write_file in the tool log, or a `git commit` there), run one bounded review of the repo's diff on the
   strong model (aux task `coach_diff_review`) and send its findings back through the pre_verify continue path.
   Bounds: one review per turn, diff capped at `diff_review_max_chars`, call timeout `diff_review_timeout_s`.

Both are OFF by default (current behaviour). Settings: env first, then config.yaml
`plugins.entries.coach.settings.<key>`:

| key | env | default |
|---|---|---|
| fixloop_review | COACH_FIXLOOP_REVIEW | false |
| fixloop_review_model | COACH_FIXLOOP_REVIEW_MODEL | claude-opus-5-5 |
| fixloop_review_provider | COACH_FIXLOOP_REVIEW_PROVIDER | "" (assignee profile's provider) |
| fixloop_review_assignee | COACH_FIXLOOP_REVIEW_ASSIGNEE | default |
| diff_review | COACH_DIFF_REVIEW | false |
| diff_review_max_chars | COACH_DIFF_REVIEW_MAX_CHARS | 60000 |
| diff_review_timeout_s | COACH_DIFF_REVIEW_TIMEOUT_S | 120 |
| diff_review_threshold | COACH_DIFF_REVIEW_THRESHOLD | reply threshold (0.7) |

The diff reviewer's model is the aux task's: `auxiliary.coach_diff_review.{provider,model}` (shared-config pins
anthropic / claude-opus-5-5). With no aux config, `fixloop_review_model` is passed as the model.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

_TRUE = ("1", "on", "true", "yes")
_FALSE = ("0", "off", "false", "no", "")

DEFAULTS = {
    "fixloop_review": False,
    "fixloop_review_model": "claude-opus-5-5",
    "fixloop_review_provider": "",
    "fixloop_review_assignee": "default",
    "diff_review": False,
    "diff_review_max_chars": 60000,
    "diff_review_timeout_s": 120,
    "diff_review_threshold": None,
}


def _config_settings() -> dict:
    try:
        from hermes_cli.config import load_config_readonly  # type: ignore
        cfg = load_config_readonly() or {}
    except Exception:
        try:
            from hermes_cli.config import load_config  # type: ignore
            cfg = load_config() or {}
        except Exception:
            return {}
    try:
        s = ((cfg.get("plugins") or {}).get("entries") or {}).get("coach") or {}
        return dict(s.get("settings") or {})
    except Exception:
        return {}


def setting(key: str, settings: Optional[dict] = None) -> Any:
    """Env `COACH_<KEY>` wins, then config `plugins.entries.coach.settings.<key>`, then DEFAULTS."""
    default = DEFAULTS.get(key)
    raw: Any = os.environ.get(f"COACH_{key.upper()}")
    if raw is None:
        raw = (settings if settings is not None else _config_settings()).get(key)
    if raw is None:
        return default
    if isinstance(default, bool):
        return raw if isinstance(raw, bool) else str(raw).strip().lower() in _TRUE
    if isinstance(default, int) or key.endswith(("_s", "_chars")):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    if key.endswith("_threshold"):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default
    return str(raw)


# ───────────────────────────── files / repos from the tool log ─────────────────────────────
_WRITE_RE = re.compile(r"^-> (?:patch|write_file|edit_file|mcp__patch|mcp__write_file)\((.+)\)\s*$")
_COMMIT_RE = re.compile(r"\bgit\b(?:\s+-C\s+(\S+))?[^|;&\n]*?\bcommit\b")
_CD_RE = re.compile(r"(?:^|[;&|]\s*|\()\s*cd\s+(\"[^\"]+\"|'[^']+'|[^\s;&|]+)")


_V4A_FILE_RE = re.compile(r"\*\*\* (?:Update|Add|Delete) File: ([^\s\\\"]+)")


def files_touched(log_lines: list) -> list[str]:
    """Paths written by patch/write_file in a coach turn log (`-> patch(<path>)` lines; a V4A multi-file patch
    logs its JSON args, so its `*** Update File:` headers are read instead), in order, unique."""
    out: list[str] = []
    for ln in log_lines or []:
        m = _WRITE_RE.match(str(ln).strip())
        if not m:
            continue
        arg = m.group(1).strip()
        paths = _V4A_FILE_RE.findall(arg) if arg.startswith("{") else [arg]
        for p in paths:
            if p and len(p) < 1024 and p not in out:
                out.append(p)
    return out


def commit_dirs(log_lines: list) -> list[str]:
    """Directories a `git commit` in the turn's terminal calls ran in (`git -C X`, else the last `cd X` before it)."""
    out: list[str] = []
    for ln in log_lines or []:
        s = str(ln).strip()
        if not s.startswith("-> terminal("):
            continue
        cmd = s[len("-> terminal("):].rstrip(")")
        for m in _COMMIT_RE.finditer(cmd):
            d = m.group(1)
            if not d:
                cds = [c.group(1).strip("\"'") for c in _CD_RE.finditer(cmd[:m.start()])]
                d = cds[-1] if cds else None
            if d and d not in out:
                out.append(d)
    return out


def _git(args: list[str], cwd: str, timeout: float = 10) -> Optional[str]:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    return r.stdout if r.returncode == 0 else None


def repo_root(path: str) -> Optional[str]:
    try:
        p = Path(os.path.expanduser(str(path)))
        if not p.is_absolute():
            return None
        d = p if p.is_dir() else p.parent
        while not d.exists() and d != d.parent:
            d = d.parent
    except (OSError, ValueError):
        return None
    top = _git(["rev-parse", "--show-toplevel"], str(d), timeout=5)
    return top.strip() if top and top.strip() else None


def changed_repos(log_lines: list, changed_paths: Optional[list] = None) -> dict[str, list[str]]:
    """{repo root: [changed files under it]} for this turn. Empty = the turn changed no git repo."""
    out: dict[str, list[str]] = {}
    for p in list(changed_paths or []) + files_touched(log_lines):
        root = repo_root(p)
        if root:
            lst = out.setdefault(root, [])
            if str(p) not in lst:
                lst.append(str(p))
    for d in commit_dirs(log_lines):
        root = repo_root(d)
        if root:
            out.setdefault(root, [])
    return out


def repo_diff(root: str, files: list[str], since: Optional[float] = None, max_chars: int = 60000) -> str:
    """This turn's change in one repo: commits made since `since` (the turn start) plus the uncommitted diff, plus
    untracked files the turn wrote. Capped at max_chars (the tail is cut and marked)."""
    base = "HEAD"
    if since:
        revs = (_git(["log", f"--since=@{int(since) - 5}", "--format=%H"], root) or "").split()
        if revs:
            oldest = revs[-1]
            base = f"{oldest}^" if _git(["rev-parse", "-q", "--verify", f"{oldest}^"], root) else oldest
    parts = []
    stat = _git(["diff", "--stat", base], root) or ""
    diff = _git(["diff", base], root) or ""
    if stat.strip():
        parts.append(stat.rstrip())
    if diff.strip():
        parts.append(diff.rstrip())
    untracked = set((_git(["ls-files", "--others", "--exclude-standard"], root) or "").split("\n"))
    for f in files:
        try:
            rel = os.path.relpath(os.path.realpath(os.path.expanduser(f)), os.path.realpath(root))
        except ValueError:
            continue
        if rel in untracked:
            try:
                txt = Path(root, rel).read_text(errors="replace")[:max_chars // 4]
            except Exception:
                continue
            parts.append(f"--- /dev/null\n+++ b/{rel} (new, untracked)\n" + "\n".join("+" + x for x in txt.splitlines()))
    out = "\n\n".join(parts)
    if len(out) > max_chars:
        out = out[:max_chars] + f"\n[... diff truncated at {max_chars} chars ...]"
    return out


# ───────────────────────────── 1. fix-loop review card ─────────────────────────────
def record_fail(state: dict, turn: int, user_text: str, issue: str) -> list[dict]:
    """Remember what each confirmed failure was about (kept in step with fix_loop's state["fails"] window)."""
    recs = [r for r in state.get("fail_recs") or [] if r.get("turn") in (state.get("fails") or [])]
    if not any(r.get("turn") == turn for r in recs):
        recs.append({"turn": turn, "ts": time.time(), "user": (user_text or "")[:1500], "issue": issue or "",
                     "ask": (state.get("last_ask") or "")[:1500],  # the request the failed claim answered
                     "claim": (state.get("last_reply") or "")[-2500:],
                     "files": files_touched(state.get("last_log") or [])})
    state["fail_recs"] = recs
    return recs


def card_args(session_id: str, recs: list[dict], ask: str = "", settings: Optional[dict] = None) -> dict:
    ask = ask or (recs[0].get("ask") if recs else "") or ""
    model = setting("fixloop_review_model", settings)
    provider = setting("fixloop_review_provider", settings)
    files: list[str] = []
    for r in recs:
        files += [f for f in r.get("files") or [] if f not in files]
    lines = [
        "A coach fix-loop check confirmed that the agent's fix failed twice in a row in session "
        f"`{session_id}` (@session:{session_id}). You are a read-only second opinion.",
        "",
        "**Trace and report what you observe; do not edit.** Do not patch, commit, deploy or restart anything. "
        "Read the files, logs and state, reproduce the user's failure if you can do so without side effects, and "
        "report: what the code/system actually does on the user's path, where it diverges from what the agent "
        "claimed, and the evidence (commands + output) for each point. Say plainly what you could not check.",
        "",
    ]
    if ask:
        lines += ["## Original ask", ask[:1500], ""]
    for i, r in enumerate(recs, 1):
        lines += [f"## Failure {i}", "**Agent claimed:**", "> " + r.get("claim", "").replace("\n", "\n> ")[:2500],
                  "", "**User replied:**", "> " + r.get("user", "").replace("\n", "\n> ")[:1500], ""]
    lines += ["## Files touched in the failing turns", *([f"- `{f}`" for f in files] or ["- (none recorded)"]), "",
              "Finish with `kanban_complete`: summary = the 1-3 most likely causes with their evidence; "
              "metadata.findings = list of {claim, observed, evidence}."]
    issue = (recs[-1].get("issue") if recs else "") or "failed fix"
    args = {"title": f"Second opinion (read-only): {issue}"[:120], "assignee": setting("fixloop_review_assignee", settings),
            "body": "\n".join(lines),
            "idempotency_key": f"coach-fixloop:{session_id}:{int(recs[0]['ts']) if recs else 0}",
            "max_runtime_seconds": 1800, "model": model}
    if provider:
        args["provider"] = provider
    if session_id:
        args["session_id"] = session_id
    return args


def create_card(args: dict) -> dict:
    """Create through the kanban_create tool handler (not the CLI): it stamps the session as the card's origin
    and auto-subscribes the calling chat, so the card shows in this chat's task strip and its completion wakes it."""
    from tools.kanban_tools import _handle_create  # type: ignore
    return json.loads(_handle_create(dict(args)))


def fixloop_review(state: dict, session_id: str, turn: int, hit: dict, user_text: str, ask: str = "",
                   create: Callable[[dict], dict] = create_card, settings: Optional[dict] = None) -> Optional[str]:
    """Called on every confirmed failure. Returns the extra hint (card id) once the loop reaches 2, else None.
    One card per loop window: the card id is cached in state and the kanban idempotency key dedupes restarts."""
    recs = record_fail(state, turn, user_text, hit.get("issue", ""))
    if hit.get("n", 0) < 2 or not setting("fixloop_review", settings):
        return None
    key = card_args(session_id, recs, ask, settings)["idempotency_key"]
    tid = (state.get("review_card") or {}).get(key)
    if not tid:
        res = create(card_args(session_id, recs, ask, settings))
        tid = res.get("task_id") if isinstance(res, dict) else None
        if not tid:
            return None
        state["review_card"] = {key: tid}
    return (f"[coach] A read-only second-opinion card `{tid}` (pinned to {setting('fixloop_review_model', settings)}) "
            f"is now tracing this failure with your claims, the user's replies and the files you touched. Don't patch "
            f"again until you've read its handoff: `kanban_show(task_id=\"{tid}\")` (you'll be notified when it "
            f"finishes). Meanwhile gather raw evidence only.")


# ───────────────────────────── 2. unverified + repo diff review ─────────────────────────────
REVIEW_SYS = """You are a second reviewer for an AI coding agent's turn. The agent is about to tell the user its
work is done. You see the user's request, the agent's final message, the turn's tool log, and the git diff the turn
produced. Trace the diff against the request and the agent's claims and report what you observe. Do not invent
problems: if the diff does what the message says, say so. Report only concrete, checkable observations:
- claims in the message that the diff or tool log does not support;
- behaviour the diff actually changes that the message does not mention;
- likely defects you can point to by file:line (wrong condition, missing case, broken caller, missing test);
- the single most direct check that would verify the claim on the user's real path.

Return ONLY JSON: {"supported": true|false, "findings": ["<=40 words each, with file:line>", ...], "check": "<one
command or step>"}  (at most 5 findings)"""


def review_prompt(ask: str, reply: str, log_lines: list, diffs: dict[str, str]) -> str:
    log = "\n".join(list(log_lines or [])[-40:]) or "(no tool calls)"
    d = "\n\n".join(f"### repo {root}\n{txt}" for root, txt in diffs.items())
    return (f"USER'S REQUEST:\n{(ask or '')[:1500]}\n\nAGENT'S FINAL MESSAGE:\n{(reply or '')[:4000]}\n\n"
            f"TOOL LOG (truncated):\n{log[:5000]}\n\nGIT DIFF THIS TURN:\n{d}")


def aux_review(system: str, prompt: str, timeout: float = 120, settings: Optional[dict] = None) -> str:
    """Aux task `coach_diff_review`; when that task has no model configured, pin fixloop_review_model."""
    from agent.auxiliary_client import call_llm  # type: ignore
    kw: dict[str, Any] = {}
    try:
        from hermes_cli.config import load_config_readonly  # type: ignore
        aux = ((load_config_readonly() or {}).get("auxiliary") or {}).get("coach_diff_review") or {}
    except Exception:
        aux = {}
    if not aux.get("model"):
        kw["model"] = setting("fixloop_review_model", settings)
        prov = setting("fixloop_review_provider", settings)
        if prov:
            kw["provider"] = prov
    resp = call_llm(task="coach_diff_review", messages=[{"role": "system", "content": system},
                                                       {"role": "user", "content": prompt}],
                    max_tokens=900, temperature=0, timeout=timeout, **kw)
    return (getattr(resp.choices[0].message, "content", None) or "").strip()


def parse_review(raw: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:
        return None
    f = [str(x)[:400] for x in (d.get("findings") or []) if str(x).strip()][:5]
    return {"supported": bool(d.get("supported")), "findings": f, "check": str(d.get("check") or "")[:300]}


def diff_review(ask: str, reply: str, log_lines: list, changed_paths: Optional[list], since: Optional[float],
                llm: Optional[Callable[..., str]] = None, settings: Optional[dict] = None,
                repos: Optional[dict] = None) -> Optional[dict]:
    """Run one bounded review of this turn's diff. None when no repo changed, the diff is empty or the call fails."""
    repos = repos if repos is not None else changed_repos(log_lines, changed_paths)
    if not repos:
        return None
    cap = setting("diff_review_max_chars", settings)
    diffs: dict[str, str] = {}
    left = cap
    for root, files in repos.items():
        if left <= 0:
            break
        txt = repo_diff(root, files, since, max_chars=left)
        if txt.strip():
            diffs[root] = txt
            left -= len(txt)
    if not diffs:
        return None
    timeout = setting("diff_review_timeout_s", settings)
    call = llm or (lambda s, p: aux_review(s, p, timeout=timeout, settings=settings))
    v = parse_review(call(REVIEW_SYS, review_prompt(ask, reply, log_lines, diffs)))
    if not v:
        return None
    v["repos"] = list(diffs)
    v["diff_chars"] = sum(len(x) for x in diffs.values())
    return v


def review_nudge(v: dict, evidence: str = "") -> str:
    head = ("[coach] Before ending: your message says it's done, but this turn has no check on the user's actual path"
            f"{f' ({evidence})' if evidence else ''}. An independent review of this turn's diff "
            f"({', '.join(Path(r).name for r in v.get('repos', []))}) reports")
    if v.get("findings"):
        body = ":\n" + "\n".join(f"- {f}" for f in v["findings"])
    else:
        body = " no concrete problems in the diff."
    tail = (f"\nMost direct check: {v['check']}" if v.get("check") else "") + (
        "\nTrace each finding yourself (they can be wrong), fix what's real, run the check on the user's path, "
        "then report with the evidence, or state plainly what is still unverified.")
    return head + body + tail

"""approval_reuse: an approval David gave stays usable until something actually ran (agent-159f5f5152).

On 2026-11-13 David approved the same Kanban priority reset five times in one chat over 40 h, then wrote "i already
approved this a bunch of times, why do you keep asking me". Each round stopped before any write on a fixable
refusal (a stale snapshot, a rotated python path), recorded the approval `not_applied`, and published a new
approval for the same machines and the same data. Two things made that the agent's default:

1. The requests made themselves single-use. Their ``attempts`` limit said "one run; on any refusal ... I stop and
   report. No rerun without asking you." so any refusal, even one before a write, became a new ask.
2. The ask-gate judge counts a request that "sets new limits" (a new map sha, checkout or launcher path) as a new
   decision, so a repeat with fresh execution details passed as `ok`.

Two deterministic holds, no model call, run before the judge on every action_request approval:

- ``single_use_clause``: limits, exclusions or outcomes that forbid a rerun without asking again. The nudge asks
  for retry terms instead ("a refusal before anything changes doesn't use this approval up").
- ``covering_approval``: David already approved, in this chat, a request that shares a source card with this one,
  carries every permission-required limit key this one has, is about the same thing (title and permission-limit
  words overlap), and nothing ran under it (its receipts never reached reserved/applied). Released by a scope line
  ``New since your approval: <permission limit key> ...`` that names a widened permission limit.

Both are bounded by the caller's per-(session, title) hold count, so a wrong hold costs at most two nudges.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable

DECISION_PREFIX = "Canonical user decision."
# A phase past these means something may have run, so a second run is a new decision.
_UNUSED_PHASES = frozenset({"acknowledged", "not_applied"})

_SINGLE_USE = re.compile(
    r"\bno\s+(?:re-?runs?|retr(?:y|ies)|second\s+(?:run|attempt|try))\s+without\s+(?:first\s+)?"
    r"(?:asking|checking\s+with|your\s+(?:ok|okay|approval|go-ahead|sign-?off)|a\s+new\s+approval|approval)"
    r"|\b(?:ask|come\s+back\s+to|check\s+with)\s+you\s+(?:again\s+)?before\s+(?:any|a|another)\s+"
    r"(?:re-?run|retry|second\s+(?:run|attempt))"
    r"|\b(?:re-?runs?|retr(?:y|ies))\s+(?:needs?|requires?)\s+(?:a\s+)?(?:new|fresh|your|another)\s+approval",
    re.I)

_NEW_SINCE = re.compile(r"^[\s>*_-]*new since your (?:earlier |last |previous )?approval\s*[*_]*\s*:\s*(.+)$",
                        re.I | re.M)

_STOP = frozenset("that this with from have what when will your they them then than there their about would "
                  "should could which only into just also make made like does done want need".split())


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9-]{3,}", (s or "").lower()) if w not in _STOP}


def _typed(d: Any, key: str, typ: type) -> Any:
    value = d.get(key) if isinstance(d, dict) else None
    return value if isinstance(value, typ) else typ()


def _limits(req: dict) -> list[dict]:
    return [x for x in _typed(req, "limits", list) if isinstance(x, dict)]


def _perm_keys(req: dict) -> set[str]:
    return {" ".join(str(x.get("key") or "").lower().split()) for x in _limits(req) if x.get("permissionRequired")}


def _cards(req: dict) -> set[str]:
    refs = _typed(_typed(req, "scope", dict), "sourceRefs", list) + _typed(req, "aliases", list)
    return {str(x.get("id")) for x in refs if isinstance(x, dict) and str(x.get("id") or "").startswith("t_")}


def single_use_clause(req: dict) -> str | None:
    """The clause that makes this approval single-use, verbatim (trimmed), or None."""
    texts = [str(x.get("value") or "") for x in _limits(req)]
    texts += [str(x) for x in _typed(req, "exclusions", list)]
    texts += [str(v) for v in _typed(req, "outcomes", dict).values()]
    for t in texts:
        m = _SINGLE_USE.search(t)
        if m:
            return " ".join(m.group(0).split())
    return None


def _decision_json(content: str) -> dict | None:
    if not isinstance(content, str) or not content.startswith(DECISION_PREFIX):
        return None
    i = content.find("{")
    if i < 0:
        return None
    try:
        obj = json.JSONDecoder().raw_decode(content[i:])[0]
    except ValueError:
        return None
    return obj if isinstance(obj, dict) and isinstance(obj.get("request"), dict) else None


def prior_approvals(rows: Iterable[tuple[str, str, Any]]) -> list[dict]:
    """Approvals David gave in this chat, from its (role, content, timestamp) rows in order.

    Returns one dict per operation: {operation, request, ts, phases}. ``phases`` are the receipt phases the
    agent's own action_request receipt calls recorded for that operation (tool results), in order."""
    approvals: dict[str, dict] = {}
    phases: dict[str, list[str]] = {}
    for role, content, ts in rows:
        if role == "user":
            d = _decision_json(content)
            if d and d.get("effect") == "approve" and d.get("executionAuthorized") is not False:
                op = str(d.get("operationId") or "")
                if op and op not in approvals:
                    approvals[op] = {"operation": op, "request": d["request"], "ts": ts}
        elif role == "tool" and isinstance(content, str) and '"receipt"' in content and '"phase"' in content:
            try:
                obj = json.loads(content)
            except ValueError:
                continue
            rec = obj.get("receipt") if isinstance(obj, dict) else None
            if isinstance(rec, dict) and rec.get("operationId") and rec.get("phase"):
                phases.setdefault(str(rec["operationId"]), []).append(str(rec["phase"]))
    for op, a in approvals.items():
        a["phases"] = phases.get(op, [])
    return list(approvals.values())


def _about(req: dict) -> set[str]:
    return _words(str(req.get("title") or "") + " " + " ".join(
        str(x.get("value") or "") for x in _limits(req) if x.get("permissionRequired")))


_HEXISH = re.compile(r"^(?:[0-9a-f]{4,}|sha\d+|\d+[a-z]{1,2})$")


def _targets(req: dict) -> set[str]:
    """Target-like tokens of the permission limits: names carrying a digit (worker-7, t_ ids, counts like 11).

    File names, paths and hashes (a new map file, checkout or launcher) are execution details, not targets."""
    out = set()
    for x in _limits(req):
        if not x.get("permissionRequired"):
            continue
        for tok in re.split(r"[\s,;:()\[\]{}\"'`*<>=+…|]+", str(x.get("value") or "").lower()):
            tok = tok.strip("-_.!?")
            if not tok or "." in tok or "/" in tok or "~" in tok or not re.search(r"\d", tok):
                continue
            if _HEXISH.match(tok):
                continue
            out.add(tok)
    return out


def widened_targets(req: dict, prior_req: dict) -> set[str]:
    """Target tokens in this request's permission limits that the earlier approval did not name."""
    return _targets(req) - _targets(prior_req)


def declared_widening(req: dict) -> str | None:
    """A 'New since your approval: <permission limit key> ...' scope line naming a permission limit, or None."""
    plan = str(_typed(req, "scope", dict).get("markdown") or "")
    keys = _perm_keys(req)
    for m in _NEW_SINCE.finditer(plan):
        line = " ".join(m.group(1).lower().split())
        if any(k and k in line for k in keys):
            return m.group(1).strip()
    return None


def covering_approval(req: dict, priors: list[dict], min_overlap: float = 0.5) -> dict | None:
    """The newest earlier approval in this chat that already covers ``req`` and was never used, or None."""
    cards, keys, about = _cards(req), _perm_keys(req), _about(req)
    if not cards or not keys or declared_widening(req):
        return None
    for p in reversed(priors):
        preq = p.get("request") or {}
        if any(ph not in _UNUSED_PHASES for ph in p.get("phases") or []):
            continue  # something may have run under it: a second run is a new decision
        if not (cards & _cards(preq)) or not keys <= _perm_keys(preq) or widened_targets(req, preq):
            continue
        pabout = _about(preq)
        if not about or not pabout or len(about & pabout) / min(len(about), len(pabout)) < min_overlap:
            continue
        return p
    return None


def _fmt_ts(ts: Any) -> str:
    try:
        import time
        return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(float(ts)))
    except (TypeError, ValueError):
        return str(ts or "?")


def single_use_message(title: str, clause: str) -> str:
    return (f"ask-gate: approval_single_use. action_request \"{title[:160]}\" not published (David has NOT seen it). "
            f"Its terms make the approval single-use (\"{clause[:200]}\"), so any fixable refusal before a write "
            "(stale snapshot, moved path, unreachable host) turns into asking David to approve the same thing again "
            "(agent-159f5f5152: he approved one reset five times). Replace that clause with retry terms, e.g. "
            "\"A refusal before anything changes doesn't use this approval up: I fix the cause and rerun under it. "
            "I come back to you only if the targets, data or spend change, or after a failure that changed "
            "something.\" Then publish again.")


def covering_message(title: str, prior: dict, keys: set[str]) -> str:
    preq = prior.get("request") or {}
    used = ", ".join(prior.get("phases") or []) or "no receipt"
    return (f"ask-gate: approval_reuse. action_request \"{title[:160]}\" not published (David has NOT seen it). "
            f"David already approved \"{str(preq.get('title') or '')[:160]}\" in this chat at "
            f"{_fmt_ts(prior.get('ts'))} (operation {str(prior.get('operation'))[:8]}; receipts: {used}). It shares "
            f"a source card with this request and the same permission limits ({', '.join(sorted(keys))}), and "
            "nothing ran under it. A refusal before any write does not use an approval up, and new execution "
            "details (paths, checkouts, file hashes, launchers) are not a new decision. Do not ask again: fix the "
            "cause and run under his approval, and tell him in one line that you are doing so. Publish again only "
            "if a permission limit widens (more targets, more data, more spend): add a scope line "
            "\"New since your approval: <limit key>: <what widened>\".")


def check(req: dict, rows_for: Callable[[], Iterable[tuple[str, str, Any]]]) -> tuple[str, str] | None:
    """(rule, message) when this approval request should be held, else None. ``rows_for`` reads the chat lazily."""
    if str(req.get("kind") or "") != "approval":
        return None
    title = str(req.get("title") or "").strip()
    clause = single_use_clause(req)
    if clause:
        return "approval_single_use", single_use_message(title, clause)
    if not _cards(req) or not _perm_keys(req):
        return None
    prior = covering_approval(req, prior_approvals(rows_for()))
    if prior:
        return "approval_reuse", covering_message(title, prior, _perm_keys(req))
    return None

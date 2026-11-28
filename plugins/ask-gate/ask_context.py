"""ask_context_hold: a human ask must give David the context and concrete steps to resolve it. [daveey]

David (hub chat 20261127_220504_7d7971, 2026-11-27): "the blocked card must have concrete actions and the context
that the user would need to resolve it. It can't just be 'unblock card, blah, blah, blah.'" Hub card
t_01a21bb1169deea2 (unforged CI, PR #13) passed unblock_card_hold with "Allow a production cache-publication fix...
Unblock = A; archive = B": it never said where the PR is, and its recommended option's only cost was "additional
agent work". Billing watchdog t_01a21ef258f83d8d asked for a support-ticket check its own acceptance ("verify the
affected workflows or document the external hold with evidence") did not need.

Deterministic, no model call. Bounded like unblock_card_hold: ``MAX_HOLDS`` holds per (session, tool, card), then
the ask files anyway (logged ``ask_context_released``) so a misread never strands a real ask. ``missing`` names
each gap:

(a) context: the Situation plus why_human hold at least two sentences, and why_human says why the agent cannot do
    it (text beyond its ``Tried:`` part);
(b) where: the ask names the place to act: a URL, numbered steps, a command (backticks, ``run_in_user_terminal``)
    or a named device/app/site. A decision (``choices``) is answered on the panel itself, but every PR, issue or
    ticket it cites by number needs its URL so David can look before he picks;
(c) decisions: each option says what happens and what it costs, with a size (minutes, $, runs, files, ...) or an
    explicit "no cost"/"no further work";
(d) the ask says what Unblock and Archive do.

``acceptance_met`` (point 4): the card's own acceptance has a "... or document/record the hold/gap" branch, so the
card can complete without David unless the ask changes money, permissions or safety.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path
from typing import Optional

_SENTENCE = re.compile(r"[^.!?\n]*[A-Za-z][^.!?\n]*(?:[.!?](?=\s|$)|\n|$)")
_TRIED = re.compile(r"\btried\s*:", re.I)
_URL = re.compile(r"\bhttps?://\S+", re.I)
_NUMBERED = re.compile(r"(?:^|[\s(])(?:step\s*)?[1-9]\s*[.)]\s+\S", re.I | re.M)
_COMMAND = re.compile(r"`[^`\n]{3,}`|\brun_in_user_terminal\b|\$\s+\w|\brun\s*:\s*\S|(?:^|\s)(?:~|/)[\w.-]+/\S", re.I)
_PLACE = re.compile(
    r"\b(?:i?phone|ipad|apple\s+watch|watch|mac(?:book)?(?:\s+(?:pro|air|mini))?|mini|laptop|desktop|browser|chrome|"
    r"safari|firefox|terminal|system\s+settings|settings|keychain|app\s+store|testflight|xcode|finder|"
    r"github(?:\.com)?|slack|gmail|calendar|1password|bitwarden|router|camera|hub\s+chrome|yubikey|"
    r"(?:the\s+)?[A-Z][\w-]+(?:\s+[A-Z][\w-]+)*\s+app)\b", re.I)
# A PR/issue cited by number without its repo or URL: David cannot open it from the panel.
_ARTIFACT = re.compile(r"(?<![\w/])(?:PR|pull\s+request|issue)\s*#?\s*(\d{1,6})\b", re.I)
_REPO_REF = re.compile(r"\b[\w.-]+/[\w.-]+\s*(?:#|\s+(?:PR|pull\s+request|issue)\s*#?\s*)(\d+)", re.I)
_URL_NUMBER = re.compile(r"https?://\S+?/(?:pull|pulls|issues|merge_requests)/(\d+)\b", re.I)
_LIST_ITEM = re.compile(r"(?m)^\s*(?:[-*\u2022]|\d+[.)]|\[[ x]\])\s+\S")
# FAIL CLOSED: any mention of money, payment, spend, billing or limits -- in any wording, negated or read-only
# included -- or of permissions/safety means acceptance_met never short-circuits the ask; it always takes the full
# context/steps/choices check (and payment asks still go through typed approvals). Deliberately no negation or
# "read-only" heuristics: five review rounds showed every refinement leaves a wording gap, and a mention that lets a
# read-only ask reach David costs far less than a missed money ask.
# Union of every term any version matched; no term is ever removed (owning chat, 02:05Z). _LIMIT_WORDS is the
# 0f4365e7 word list verbatim; _LIMIT_STEMS adds case-insensitive SUBSTRING stems, so every inflection counts
# ("rate-limited", "capped", "unlimited", "limitation", "repaid"). Over-matching ("capability", "escape") is the
# intended fail-closed direction; only very short or ambiguous tokens get a word boundary (eur in "neural", fee in
# "feedback", currency codes, cent, tip, fine, eth).
_LIMIT_WORDS = re.compile(
    r"\$\s?\d|\b\d+(?:\.\d+)?\s?(?:usd|dollars?|eur|euros?|gbp)\b|"
    r"\b(?:pay\w*|paid|money|spend\w*|spent|billing|bill(?:s|ed)?|invoic\w*|subscri\w*|budget\w*|refund\w*|"
    r"overage\w*|charg\w*|purchas\w*|buy\w*|bought|price\w*|pricing|fees?|credit\s+card|card\s+on\s+file|"
    r"limits?|quotas?|caps?|plan\s+(?:upgrade|change))\b|"
    r"\b(?:grant\w*|permissions?|credentials?|widen\w*|admin|sudo|api\s+keys?|scope\s+change)\b|"
    r"\b(?:delet\w*|destroy\w*|destructive|irreversible|wipe\w*|terminat\w*|safety)\b", re.I)
_LIMIT_STEMS = re.compile(
    r"[$€£¥₹₩₣₽₿]|limit|cap|quota|spend|spent|budget|pay|paid|bill|charg|cost|price|pricing|usd|gbp|jpy|\beur\b|"
    r"\d\s?eur|eur\s?\d|credit|refund|subscri|invoice|money|purchas|overage|\bfees?\b|expens|reimburs|top[- ]?up|"
    r"renew|licen[cs]|buy|bought|dollar|buck|card|plan|upgrad|downgrad|premium|tier|seat|trial|crypto|"
    r"\b(?:cents?|yen|cad|aud|inr|chf|btc|eth|tips?|fines?|dues)\b|fund|deposit|donat|wire|order|checkout|"
    r"check[\s-]out|\bcarts?\b|stripe|"
    r"venmo|paypal|tax|stipend|salar|bonus|outlay|overrun|\brent|rental|leas|reserv|ceiling|max|threshold|"
    r"allowance|throttl|"
    r"\b(?:grant\w*|permissions?|credentials?|widen\w*|admin|sudo|api\s+keys?|scope\s+change)\b|"
    r"\b(?:delet\w*|destroy\w*|destructive|irreversible|wipe\w*|terminat\w*|safety)\b", re.I)
_LIMIT_PARTS = (_LIMIT_WORDS, _LIMIT_STEMS)
_SIZE = re.compile(
    r"\d|~|\$|\b(?:one|two|three|four|five|ten|a\s+few|few|single|a\s+(?:minute|moment|second|click|tap|reply|"
    r"restart|run|day|week|hour))\b|\b(?:seconds?|minutes?|mins?|hours?|hrs?|days?|weeks?|clicks?|taps?)\b", re.I)
_NO_COST = re.compile(
    r"\bno\s+(?:further\s+|more\s+|extra\s+|new\s+)?(?:cost|work|spend(?:ing)?|money|change|changes|restart|risk|"
    r"effort|action|charge|payment)\b|\bnothing\s+(?:changes|restarts|runs|happens)\b|\bfree\b|\bzero\s+cost\b"
    r"|\bcosts?\s+nothing\b|\bno\s+cutover\b", re.I)
_BTN = r"\s*(?:\([^)\n]{1,30}\)\s*)?(?:=|:|->|→|means|will|"
_UNBLOCK = re.compile(r"\bunblock(?:ing)?\b" + _BTN + r"runs|resumes|does|starts|lets)", re.I)
_ARCHIVE = re.compile(r"\barchiv(?:e|ing)\b" + _BTN + r"stops|drops|does|ends|closes|leaves)", re.I)
_ACCEPT = re.compile(r"(?:^|\n|\.\s)\s*(?:\*\*)?(?:acceptance(?:\s+criteri(?:a|on))?|done\s+(?:when|means))"
                     r"(?:\*\*)?\s*:(?:\*\*)?\s*(.+?)(?=\n\s*\n|\n#|$)", re.I | re.S)
_DOC_BRANCH = re.compile(
    r"\bor\b[^.\n]{0,80}?\b(?:document|record|report|note|log|write\s+up)\w*\b[^.\n]{0,80}?"
    r"\b(?:hold|holds|blocker|gap|gaps|unverified|external|limitation|evidence|why)\b", re.I)
_MAX_SITUATION_SENTENCES = 2
MAX_HOLDS = 3
ENV_SWITCH = "ASK_GATE_ASK_CONTEXT"  # test isolation only, honoured only under pytest

HOLD_MESSAGE = (
    "ask-gate: ask_context_hold. David must be able to resolve this card from its panel alone, without opening the "
    "card or asking an agent. Missing: {missing}. Rewrite the human_request (generate-unblock-card): 'Situation:' "
    "says what is stuck and why_human says why you cannot do it (two or more sentences between them); the action "
    "names where to act (a URL, numbered steps '1) ... 2) ...', a command for run_in_user_terminal, or the device/"
    "app); each option says what happens and what it costs (minutes, $, runs, or 'no further work'); every PR/"
    "issue/ticket cited by number carries its URL; and the action says what Unblock and Archive do. Then file it "
    "again."
)
ACCEPTANCE_MESSAGE = (
    "ask-gate: acceptance met without this; complete instead. This card's acceptance has an alternative that "
    "needs no human step: \"{quote}\". Record what you could not verify, why, and the evidence (that is the "
    "documented hold), then kanban_complete. Ask David only when the step changes money, permissions or safety, "
    "and name that limit in why_human."
)


def _load(name: str, path: Path):
    key = f"ask_gate_ask_context_{name}_{path}"
    mod = sys.modules.get(key)
    if mod is None:
        spec = importlib.util.spec_from_file_location(key, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load {path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod = sys.modules.setdefault(key, mod)
    return mod


_quality = _load("quality", Path(__file__).resolve().with_name("unblock_card_quality.py"))
_replan = _load("replan", Path(__file__).resolve().with_name("replan.py"))


def sentences(text: str) -> list[str]:
    """Sentences of at least three words; a label line ("Tried:") alone is no sentence."""
    out = []
    for m in _SENTENCE.finditer(str(text or "")):
        s = m.group(0).strip()
        if len(re.findall(r"[A-Za-z][\w'-]*", s)) >= 3:
            out.append(s)
    return out


def _why_reason(why_human: str) -> str:
    """why_human without its ``Tried:`` clause: the part that says why only David can."""
    text = str(why_human or "").strip()
    m = re.search(r"\b(?:only\s+(?:you|david|a\s+person|a\s+human|your)\b|because|requires?|needs?|cannot|can't|"
                  r"can\s+not|outside|refuse[sd]?|not\s+allowed|no\s+(?:other\s+)?(?:supported\s+)?(?:route|way))",
                  text, re.I)
    if m:
        return text[m.start():]
    if not _TRIED.match(text):
        return text
    rest = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)
    return rest[1] if len(rest) > 1 else ""


def _option_details(request: dict) -> list[tuple[str, str]]:
    parts = _quality.sections(str(request.get("scope") or ""))
    items = _quality.options(parts["options"]) if "options" in parts else []
    out = []
    for item in items:
        got = _quality._brief._option_parts(item)
        name = str(got.get("name") or "").strip() if isinstance(got, dict) else ""
        detail = str(got.get("detail") or "").strip() if isinstance(got, dict) else item
        out.append((name or item[:40], detail))
    return out


def _recommended(request: dict, details: list[tuple[str, str]]) -> Optional[tuple[str, str]]:
    """The option the ask recommends (``Recommend: A`` or ``(recommended)``), else None."""
    if not details:
        return None
    scope = str(request.get("scope") or "")
    rec = _quality.sections(scope).get("recommend", "")
    m = re.match(r"\s*(?:option\s+)?\(?([A-F])\b", rec, re.I)
    if m:
        idx = ord(m.group(1).upper()) - ord("A")
        if 0 <= idx < len(details):
            return details[idx]
    labels = [str(c.get("label") or "") for c in request.get("choices") or [] if isinstance(c, dict)]
    for i, label in enumerate(labels):
        if "recommended" in label.lower() and i < len(details):
            return details[i]
    return None


def missing(request: dict) -> list[str]:
    """Names of the missing parts; empty when the ask carries context, place, costs and button meanings."""
    if not isinstance(request, dict):
        return []
    action = str(request.get("action") or "")
    why = str(request.get("why_human") or "")
    scope = str(request.get("scope") or "")
    choices = request.get("choices") if isinstance(request.get("choices"), list) else []
    contract = _quality.contract_scope(scope)
    situation = action if contract else _quality.sections(scope).get("situation", "")
    reason = _why_reason(why)
    out: list[str] = []
    # (a) context
    if len(sentences(situation)) + len(sentences(why)) < 2 or len(_quality._flat(reason)) < 20:
        out.append("context: 'Situation:' plus why_human need two or more sentences saying what is stuck and why "
                   "you cannot do it")
    # (b) where
    text = "\n".join((action, scope))
    decision = _quality.is_decision(request)
    placed = bool(_URL.search(text) or _NUMBERED.search(action) or _COMMAND.search(text) or _PLACE.search(action))
    if not placed and not decision:
        out.append("where: the action names no place to act (URL, numbered steps, command, device or app)")
    every = "\n".join((action, scope, why))
    cited = sorted({m.group(1) for m in _ARTIFACT.finditer(every)}, key=int)
    located = {m.group(1) for m in _URL_NUMBER.finditer(every)} | {m.group(1) for m in _REPO_REF.finditer(every)}
    unlinked = [n for n in cited if n not in located]
    if unlinked:
        out.append("where: PR/issue " + ", ".join("#" + n for n in unlinked) + " is cited without its URL")
    # (c) decisions: every option names its consequence and cost
    if decision:
        details = _option_details(request)
        if not details and choices:
            details = [(str(c.get("label") or ""), str(c.get("explain") or "")) for c in choices if isinstance(c, dict)]
        thin = [name for name, detail in details
                if len(_quality._flat(detail)) < 8 or len(_quality._flat(f"{name} {detail}")) < 30]
        if thin:
            out.append("choices: option(s) " + "; ".join(n[:40] for n in thin) + " do not say what happens")
        picked = _recommended(request, details)
        if picked and not thin and not (_SIZE.search(picked[1]) or _NO_COST.search(picked[1])):
            out.append(f"choices: the recommended option {picked[0][:40]} states no size or cost (minutes, $, "
                       "runs, files, or 'no further work')")
    # (d) Unblock and Archive
    buttons = "\n".join((action, scope))
    if not _UNBLOCK.search(buttons) or not _ARCHIVE.search(buttons):
        out.append("buttons: the action does not say what Unblock and Archive do ('Unblock = ...; archive = ...')")
    return out


def acceptance_alternative(body: str) -> Optional[str]:
    """The card acceptance's "or document the hold" branch, quoted, when it covers the WHOLE acceptance: a single
    sentence with no other listed item. A doc branch on one item of several leaves the others owed. Else None."""
    acceptances = list(_ACCEPT.finditer(str(body or "")))
    if len(acceptances) != 1:
        return None
    clause = acceptances[0].group(1).strip()
    if _LIST_ITEM.search(clause) or len(sentences(clause)) != 1 or not _DOC_BRANCH.search(clause):
        return None
    return " ".join(clause.split())[:240]


def _limit_named(text: str) -> bool:
    """The ask mentions money, payment, spend, billing, limits, permissions or safety -- in any wording (fail closed)."""
    text = str(text or "")
    return any(p.search(text) for p in _LIMIT_PARTS) or \
        _replan.limit_named(text) in ("spend", "permission", "safety")


def acceptance_met(request: dict, body: str) -> Optional[str]:
    """The quoted acceptance clause when the card can complete without this ask, else None."""
    quote = acceptance_alternative(body)
    if quote is None or not isinstance(request, dict):
        return None
    if _limit_named("\n".join(str(request.get(k) or "") for k in ("action", "why_human", "scope"))):
        return None
    return quote


def hit(request: dict, body: str = "") -> Optional[dict]:
    """None when the ask may be filed, else ``{"rule", "missing", "message"}``."""
    if os.environ.get(ENV_SWITCH) == "0" and os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    quote = acceptance_met(request, body)
    if quote is not None:
        return {"rule": "acceptance_met", "missing": [], "message": ACCEPTANCE_MESSAGE.format(quote=quote)}
    gaps = missing(request)
    if not gaps:
        return None
    return {"rule": "ask_context_hold", "missing": gaps, "message": HOLD_MESSAGE.format(missing="; ".join(gaps))}

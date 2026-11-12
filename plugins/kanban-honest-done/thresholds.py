"""Gate G: a failed acceptance threshold never becomes an automatic waiver or a guessed pass.

Why (red-sweep slug ``acceptance-waiver-owner-decision``, 2026-11-12): three cards hit a fixed threshold that failed
(an energy filter where no source passed checks 1 and 4, an oracle bar of 800 against a measured 807.35,
a 25%-free disk target against 6.1%). The next step offered in each was a waiver or a different test chosen after
the result. Gate F already refuses a card *body* edit that waives acceptance without a user message typed in a
human chat. This gate covers the other exit: a worker completing (or handing to review) its own card with a
handoff that waives the failed check or reports it as passing.

Refused for the worker's OWN card (``kanban_complete``, ``hermes kanban complete``, ``kanban_request_review``):

Structured ``metadata.acceptance_results`` (a list of ``{check, status, threshold?, comparator?, observed?,
branch?}``, or a ``{check: {...}}`` dict):
- ``pass`` without ``observed`` evidence, or whose numeric ``observed`` contradicts ``comparator threshold``, or is
  in other units than the threshold (a guessed pass);
- ``fail`` / ``not_run`` unless ``branch`` cites a failure branch the card body preregistered for that check
  (``IF FAIL <check>: <next step>`` line; ``IF FAIL:`` with no label covers every check). A branch whose step
  waives or passes the check is not a branch: no preregistration can turn a fail into a pass;
- ``waived`` unless the current body records a waiver naming that check (a Gate-F waiver line, which only a
  user message typed in a human chat can add);
- any other status.

Prose (summary + every metadata text value, quoted text and questions skipped): a sentence that reports a failed or
missed check AND waives or accepts it, or that reports a pass by judgment ("treated as a pass", "close enough",
"effectively passes"). A waiver sentence is allowed when the body records a human waiver; a guessed pass never is.

What it does not judge: whether a preregistered branch is wise (review it before execution), whether the cited
human words authorize the change (Gate F proves only who said them), or results that never reach a handoff.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

try:
    from .acceptance import WAIVER_RE, waiver_regions
except ImportError:  # loaded without a parent package (pytest, hyphenated dir)
    import importlib.util
    import sys
    from pathlib import Path

    _key = "kanban_honest_done_acceptance"
    _acc = sys.modules.get(_key)
    if _acc is None:
        _spec = importlib.util.spec_from_file_location(_key, Path(__file__).with_name("acceptance.py"))
        _acc = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_acc)
        _acc = sys.modules.setdefault(_key, _acc)
    WAIVER_RE, waiver_regions = _acc.WAIVER_RE, _acc.waiver_regions

STATUSES = ("pass", "fail", "not_run", "waived")
_STATUS_ALIASES = {"passed": "pass", "ok": "pass", "failed": "fail", "not run": "not_run", "not-run": "not_run",
                   "skipped": "not_run", "waive": "waived"}

# Metadata subtrees that are not threshold reports (a PR-contract waiver concerns absent required CI checks), and
# acceptance_results fields that are copied from the body (`branch`) or are values, not claims.
_SKIP_KEYS = {"contract_waiver", "branch", "check", "status", "comparator", "threshold"}

# Not on ";": "check 4 failed (82%); waived" is one claim split over two clauses.
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_QUOTED = re.compile(r"\"[^\"\n]{0,400}\"|\u201c[^\u201d\n]{0,400}\u201d|`[^`\n]{0,400}`|(?<!\w)'[^'\n]{1,120}'(?!\w)")

# The sentence reports that a check / threshold / target was not met.
FAILED_RE = re.compile(
    r"\bfail(?:s|ed|ing|ures?)?\b|\bFAIL\b|\bmiss(?:es|ed)\b|\bshort of\b|\bexceed(?:s|ed)?\b|\bover the (?:limit|bar|cap)\b"
    r"|\bunder the (?:target|floor|bar)\b|\b(?:below|above) (?:the |its )?(?:target|threshold|limit|bar|floor|cap)\b"
    r"|\bnot (?:been )?met\b|\bunmet\b|\bout of (?:range|bounds|spec)\b"
    r"|\b(?:did|does|do|could|would) ?n[o']t (?:quite )?(?:pass|meet|reach|clear)\b", re.I)
# ... and waives or accepts it.
WAIVE_RE = re.compile(
    r"\bwaiv(?:e|ed|es|ing|er|ers)\b(?!\s+(?:tests?|path|feature|flag|code|field)\b)"
    r"|\baccept(?:ed|ing|s)?\s+(?:the\s+|this\s+|that\s+|these\s+|those\s+|both\s+|it\s+)?"
    r"(?:\w+\s+){0,3}?(?:failures?|miss(?:es)?|shortfalls?|exploit|gap|overshoot)\b"
    r"|\b(?:accept(?:ed)?|land(?:ed)?|ship(?:ped)?|proceed(?:ed)?)\s+(?:it\s+|them\s+)?(?:anyway|regardless|despite)\b"
    r"|\b(?:ignore[sd]?|overrid(?:e|es|den)|relax(?:ed)?|lower(?:ed)?)\s+(?:the\s+)?(?:\w+\s+){0,2}?"
    r"(?:check|threshold|target|limit|bar|gate|floor)s?\b"
    r"|\b(?:deemed|considered|treated\s+as|judged|still|is|was|are|were)\s+(?:an?\s+)?acceptable\b"
    r"|\bcan\s+live\s+with\b|\bdropped\s+(?:it\s+|them\s+)?from\s+(?:the\s+)?acceptance\b", re.I)
# Only an explicit "waived/accept"-type line names a waiver on its own, without a failure word in the sentence; used
# for the sentence right after one that reports a failure ("Check 4 failed. Waived.").
WAIVE_ONLY_RE = re.compile(r"^\W*(?:(?:it|this|that|they|both)\s+(?:is|was|are|were)\s+|(?:it|that|they)'(?:s|re)\s+)?"
                           r"(?:waived|accepted|acceptable|accepting(?:\s+it)?)"
                           r"(?:\s*[.!]?\s*$|\s+(?:for\s+(?:this|the|that)\s+\w+|for\s+now|as\s+is|anyway|regardless)\b)",
                           re.I)
# A pass by judgment rather than measurement. Never exempt.
GUESSED_PASS_RE = re.compile(
    r"\b(?:treat(?:ed|ing|s)?|count(?:ed|ing|s)?|consider(?:ed|ing|s)?|deem(?:ed|ing|s)?|round(?:ed|ing|s)?)\s+"
    r"(?:[\w.%-]+\s+){0,4}?as\s+(?:a\s+)?pass(?:ed|ing|es)?\b"
    r"|\b(?:call(?:ed|ing|s)?|mark(?:ed|ing|s)?|record(?:ed|ing|s)?|scor(?:ed|ing|es))\s+"
    r"(?:it\s+|this\s+|that\s+|them\s+|(?:the\s+)?(?:\w+\s+){0,2}?(?:check|result|gate|threshold|target)\s+)?"
    r"as\s+(?:a\s+)?pass(?:ed|ing|es)?\b"
    r"|\bcounts?\s+as\s+(?:a\s+)?pass\b"
    r"|\b(?:effectively|essentially|practically|basically|nearly|almost|roughly|morally)\s+pass(?:es|ed|ing)?\b"
    r"|\bclose\s+enough\b|\bpass(?:es|ed)?\s+in\s+spirit\b|\bgood\s+enough\s+to\s+pass\b"
    r"|\b(?:presum(?:e|ed|ably)|assum(?:e|ed|ing))\s+(?:(?:it|this|that|the\s+\w+)\s+)?(?:to\s+)?pass(?:es|ed)?\b"
    r"|\b(?:consider|deem|treat|count|call)(?:ed|s|ing)?\s+(?:it|this|that|them|(?:the\s+)?(?:check|gate|test)\s+\w+)"
    r"\s+(?:to\s+be\s+|as\s+)?(?:a\s+)?pass(?:ed|ing)?\b(?!\s+(?:when|if|unless|on|once|for)\b)",
    re.I)
# "passes within tolerance" is a guessed pass only in a sentence comparing a number with a threshold ("807.35 vs
# < 800 target; passes within tolerance"); a float test passing within its tolerance is a measurement.
PASS_TOL_RE = re.compile(r"\bpass(?:es|ed|ing)?\s+within\s+(?:the\s+)?(?:noise|tolerance|margin|rounding)\b", re.I)
_THRESH_WORD = re.compile(r"\d\s*%?\s*(?:vs\.?|versus|against)\s*[<>=\u2264\u2265]*\s*\d"
                          r"|\b(?:target|threshold|bar|limit)\s+(?:of\s+)?[<>=\u2264\u2265]*\s*\d", re.I)
# "within noise" is a guessed pass only about a check that failed ("807 misses the 800 bar but is within noise");
# alone it is an ordinary measurement statement ("flips 43 vs 49, within noise").
NEAR_MISS_RE = re.compile(r"\bwithin\s+(?:the\s+)?(?:noise|margin|rounding)\b", re.I)
# Negation governing the hit: at most three words before it ("never waived", "no waiver", "cannot become an
# automatic waiver", "did not waive"), or a negated list ending in "or <hit>" ("never extend the lease, mock success
# or waive failures"). A negation elsewhere in the sentence ("did not pass at 82%, so I waived it") does not count.
_NEG_BEFORE = re.compile(
    r"\b(?:no|not|never|without|nor|cannot|can't|won't|didn't|doesn't|isn't|wasn't|refus\w*|instead of|rather than"
    r"|nothing(?: was| is)?|neither)\s+(?:[\w-]+\s+){0,3}$"
    r"|\b(?:no|not|never|without|didn't|cannot)\b[^.;]{0,80}\b(?:or|nor)\s+$", re.I)

_BRANCH_RE = re.compile(r"^\s*[-*]?\s*(?:\*\*)?IF\s+(?:IT\s+)?FAIL(?:S|ED)?\b(?:\*\*)?\s*([^:\n]{0,80}?)\s*:\s*(.+)$",
                        re.I | re.M)
# "800", "25%", ">= 800", "<= 80 %", "25% free", "15 GB free": optional comparator, number, unit (first token).
_NUM_RE = re.compile(r"^\s*(<=|>=|==|<|>|=|\u2264|\u2265)?\s*([-+]?\d+(?:\.\d+)?)\s*(%|[A-Za-z]+)?(?:\s+[\w\s-]*)?$")
_CMP = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
        "==": lambda a, b: a == b, "=": lambda a, b: a == b, "\u2264": lambda a, b: a <= b,
        "\u2265": lambda a, b: a >= b}
# Not evidence of a measurement.
_NO_EVIDENCE = re.compile(r"^\W*(?:n/?a|none|null|unknown|tbd|todo|pending|-+|\?+|not measured|assumed|expected|"
                          r"should pass|looks? (?:fine|good|ok))\W*$", re.I)
# Check labels named in prose or a waiver line: "check 4", "checks 1 and 4", "bullet 5b", "5b/5c".
_LABEL_RE = re.compile(r"\b(?:checks?|tests?|gates?|criteri(?:on|a)|bullets?|items?)\s+"
                       r"((?:\d+[a-z]?|[a-z]\d+[a-z]?)(?:\s*(?:,|/|&|and|or)\s*(?:\d+[a-z]?|[a-z]\d+[a-z]?))*)\b"
                       r"|\b(\d+[a-z])\b", re.I)
_GENERIC = {"check", "checks", "test", "tests", "gate", "threshold", "target", "acceptance", "result", "all", "it"}


def _norm(s: Any) -> str:
    return " ".join(str(s or "").lower().split())


def _hits(rx: re.Pattern, s: str) -> bool:
    for m in rx.finditer(s):
        if not _NEG_BEFORE.search(s[max(0, m.start() - 120):m.start()]):
            return True
    return False


def _text(metadata: Any) -> str:
    lines: list[str] = []

    def walk(v: Any, key: str = "") -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                if str(k) not in _SKIP_KEYS:
                    walk(x, str(k))
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x, key)
        elif isinstance(v, str) and v.strip():
            lines.append(v)

    walk(metadata)
    return "\n".join(lines)


def prose_hits(text: str) -> list[tuple[str, str]]:
    """``[(kind, sentence)]``: ``waiver`` (a failed check waived/accepted) or ``guessed pass``."""
    out: list[tuple[str, str]] = []
    prev_failed = False
    for sentence in _SENT_SPLIT.split(_QUOTED.sub('""', text or "")):
        s = sentence.strip()
        if not s or s.endswith("?"):
            prev_failed = False
            continue
        failed = bool(FAILED_RE.search(s))
        if _hits(GUESSED_PASS_RE, s) or (failed and _hits(NEAR_MISS_RE, s)) \
                or (_THRESH_WORD.search(s) and _hits(PASS_TOL_RE, s)):
            out.append(("guessed pass", s[:240]))
        elif (failed and _hits(WAIVE_RE, s)) or (prev_failed and not failed and WAIVE_ONLY_RE.search(s)):
            out.append(("waiver", s[:240]))
        prev_failed = failed
    return out


def labels(text: str) -> set[str]:
    """Check labels a text names: ``{"4", "1"}`` for "checks 1 and 4", ``{"5b"}`` for "5b"."""
    out: set[str] = set()
    for m in _LABEL_RE.finditer(text or ""):
        if m.group(1):
            out |= {x.lower() for x in re.findall(r"\d+[a-z]?|[a-z]\d+[a-z]?", m.group(1), re.I)}
        elif m.group(2):
            out.add(m.group(2).lower())
    return out


def _names(name: str) -> tuple[set[str], str]:
    """A check's labels, else its normalized name when that is specific enough to match on its own."""
    lab = labels(name)
    key = _norm(name)
    return lab, ("" if lab or key in _GENERIC or len(key) < 3 or not re.search(r"[a-z]", key) else key)


def _same_check(a: str, b: str) -> bool:
    """Whole-label match: "check 4" == "Check 4 (rain share)", never "check 1" == "check 14"."""
    la, ka = _names(a)
    lb, kb = _names(b)
    if la or lb:
        return bool(la) and la == lb
    return bool(ka) and ka == kb


def _waived_in(name: str, waiver_lines: list[str]) -> bool:
    lab, key = _names(name)
    for line in waiver_lines:
        if lab and lab <= labels(line):
            return True
        if key and re.search(r"(?<![\w.-])" + re.escape(key) + r"(?![\w-])", _norm(line)):
            return True
    return False


def preregistered_branches(body: str) -> list[tuple[str, str]]:
    """``[(label, step)]`` for each ``IF FAIL <label>: <step>`` line of the card body."""
    return [(_norm(m.group(1)), m.group(2).strip()) for m in _BRANCH_RE.finditer(body or "")]


_WAIVING_STEP = re.compile(
    r"\b(?:report|mark|count|treat|record)\w*\s+(?:it\s+|them\s+)?(?:as\s+)?(?:a\s+)?pass"
    r"|\b(?:complete|close|finish)\w*\s+(?:the\s+card\s+|it\s+)?(?:anyway|regardless)\b"
    r"|\bmark\w*\s+(?:the\s+card\s+|it\s+)?(?:as\s+)?done\b|\baccept\w*\s+(?:the\s+)?result\b", re.I)


def _waives(step: str) -> bool:
    return bool(_hits(WAIVE_RE, step) or _hits(GUESSED_PASS_RE, step) or WAIVER_RE.search(step)
                or _WAIVING_STEP.search(step))


def _results(metadata: Any) -> Optional[list[dict]]:
    raw = metadata.get("acceptance_results") if isinstance(metadata, dict) else None
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = [dict(v, check=v.get("check", k)) if isinstance(v, dict) else {"check": k, "status": v}
               for k, v in raw.items()]
    if not isinstance(raw, list):
        return [{"check": "acceptance_results", "status": "<malformed>"}]
    return [r if isinstance(r, dict) else {"check": str(r), "status": "<malformed>"} for r in raw]


def _num(v: Any) -> Optional[tuple[str, float, str]]:
    """``(comparator, value, unit)`` for a number-like value, else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return "", float(v), ""
    m = _NUM_RE.match(str(v or ""))
    return (m.group(1) or "", float(m.group(2)), (m.group(3) or "").lower()) if m else None


def _check_pass(name: str, r: dict) -> Optional[str]:
    observed = r.get("observed")
    if observed is None or (isinstance(observed, str) and (not observed.strip() or _NO_EVIDENCE.match(observed))):
        return f"{name}: status pass with no observed value. A pass is a measurement, not a judgment"
    if r.get("threshold") is None:
        return None
    t, o = _num(r.get("threshold")), _num(observed)
    cmp = str(r.get("comparator") or "").strip() or (t[0] if t else "")
    if not cmp:
        return (f"{name}: a threshold with no comparator can't be checked; give `comparator` (< <= > >= ==) or "
                "write the threshold as \">= 800\"")
    if cmp not in _CMP:
        return f"{name}: unknown comparator {cmp!r} (use < <= > >= ==)"
    if t is None or o is None:
        return f"{name}: threshold/observed are not numbers with a unit, so the pass can't be checked"
    if t[2] != o[2]:
        return (f"{name}: observed {observed!r} is in other units than the threshold {r.get('threshold')!r}. "
                "Report it against the card's own threshold; a different target is a test/target change for David")
    if not _CMP[cmp](o[1], t[1]):
        return (f"{name}: guessed pass: observed {observed!r} does not satisfy {cmp} {r.get('threshold')!r}. "
                "Report status fail")
    return None


def _check_fail(name: str, r: dict, branches: list[tuple[str, str]]) -> Optional[str]:
    covering = [(lab, step) for lab, step in branches if not lab or _same_check(lab, name)]
    if not covering:
        return (f"{name}: status {r.get('status')} but the card body preregisters no `IF FAIL {name}: <next step>` "
                "branch for it")
    usable = [(lab, step) for lab, step in covering if not _waives(step)]
    if not usable:
        return (f"{name}: its preregistered branch waives or passes the check, and no branch can do that; a waiver "
                "needs David's explicit answer (Gate F)")
    cited = _norm(r.get("branch"))
    if not cited:
        return f"{name}: status {r.get('status')} must cite the preregistered branch it followed in `branch`"
    for lab, step in usable:
        step_n = _norm(step)
        if len(step_n) >= 12 and step_n in cited:  # quotes the branch's whole step
            return None
        if lab and (cited == lab or (cited.startswith("if fail")
                                     and _same_check(cited[len("if fail"):].split(":", 1)[0], lab))):
            return None  # names the branch's own label: "IF FAIL check 4"
    return f"{name}: `branch` {r.get('branch')!r} is not one of the body's IF FAIL lines for this check"


def _check_waived(name: str, waiver_lines: list[str]) -> Optional[str]:
    if _waived_in(name, waiver_lines):
        return None
    return (f"{name}: status waived, but the card body records no waiver naming this check. A waiver of a failed "
            "check is a test/target change: only David's explicit answer can make it, recorded in the body with "
            "the `user message <id>` it came from (Gate F)")


def check_thresholds(task_id: str, summary: str, metadata: Any, body: str) -> Optional[str]:
    """Refusal message for a handoff that waives a failed threshold or reports a guessed pass, or None."""
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            pass
    waiver_lines = waiver_regions(body or "")
    problems: list[str] = []
    results = _results(metadata)
    if results is not None:
        branches = preregistered_branches(body)
        for r in results:
            name = str(r.get("check") or "").strip() or "<unnamed check>"
            status = _norm(r.get("status"))
            status = _STATUS_ALIASES.get(status, status)
            if status == "pass":
                why = _check_pass(name, r)
            elif status in ("fail", "not_run"):
                why = _check_fail(name, dict(r, status=status), branches)
            elif status == "waived":
                why = _check_waived(name, waiver_lines)
            else:
                why = f"{name}: status {r.get('status')!r} is not one of {', '.join(STATUSES)}"
            if why:
                problems.append(why)
    meta_text = _text(metadata) if isinstance(metadata, (dict, list)) else (metadata or "")
    for kind, sentence in prose_hits("\n".join(x for x in (summary or "", meta_text) if x)):
        # A waiver sentence is fine only when every check it names is waived in the body (and it names one).
        named = labels(sentence)
        if kind == "waiver" and named and all(_waived_in(f"check {n}", waiver_lines) for n in named):
            continue
        problems.append(f"[{kind}] \"{sentence}\"")
    if not problems:
        return None
    return refusal(task_id, problems)


def refusal(task_id: str, problems: list[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems[:5])
    return (
        f"[kanban-honest-done] Refused: this handoff turns a failed acceptance threshold of {task_id} into a waiver "
        f"or a guessed pass:\n{listed}\n"
        "A failed threshold stays failed. Do one of these instead:\n"
        "1) Report it as failed (metadata.acceptance_results status fail, with observed and threshold) and follow "
        "the card's preregistered `IF FAIL <check>:` branch, citing it in `branch`.\n"
        "2) No preregistered branch: kanban_block kind=needs_input with the measured result and the options; the "
        "human_request names the changed limit (\"waives check 4\", \"lowers the 25% target\"). Only David's "
        "explicit answer changes a test or target, and it is recorded in the body with its `user message <id>`.\n"
        "3) If the work itself is unfinished, keep going or kanban_request_review the honest result.\n"
        "Don't edit the card, the test or the threshold to make it pass. (If a flagged sentence describes code or a "
        "defect rather than this card's result, quote those words.)"
    )

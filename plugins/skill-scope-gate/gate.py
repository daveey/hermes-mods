"""Classifier for skill-scope-gate: find added sentences that are unscoped "don't act" rules.

Pure functions (stdlib only) so the hook, the tests and the git dry run share one implementation.

A sentence is a HIT when it adds
  * an authorization requirement ("without authorization", "wait for approval", "get it confirmed"),
  * a scope-narrowing mode ("diagnosis-only", "report-only", "report ... instead"), or
  * a prohibition on acting ("do not / never / must not" + an action verb: start, restart, fix, deploy ...)
and has no scoping condition in the SAME sentence ("when the card says diagnosis-only, ...", "for runner
investigations, ...", "during an incident review, ..."). A condition that is itself an authorization
("unless approved", "until David confirms") is not a scope.

Prohibitions whose object is a code span (``never `git commit -a` ``) are technical pitfalls, not blanket
"don't act" rules, and pass; authorization/mode markers are flagged even then.
"""

from __future__ import annotations

import re
from typing import Iterable

_ACT_VERBS = (
    r"start|restart|stop|kill|fix|repair|apply|change|modify|mutate|edit|write|deploy|ship|merge|push|commit|"
    r"run|rerun|re-run|launch|build|rebuild|install|uninstall|delete|remove|cancel|clean|purge|rotate|"
    r"re-?register|create|file|open|act|proceed|retry|touch|update|upgrade|reboot|elevate|enable|disable|"
    r"send|post|reset|revert|force-push|drain|resume|implement|patch|attempt|intervene|recover|restore"
)
_NEG = r"(?:do not|don't|dont|never|must not|mustn't|should not|shouldn't|avoid|refrain from)"

# (label, pattern). Authorization / mode markers: flagged even when the object is code.
_AUTH_PATTERNS = [
    ("without-authorization", re.compile(
        r"\bwithout\s+(?:\w+\s+){0,3}?(?:authori[sz]ation|approval|permission|confirmation|sign-?off|"
        r"go-?ahead|consent|being asked)\b", re.I)),
    ("authorization-required", re.compile(
        r"\b(?:requires?|needs?|get|obtain|ask for|wait for|await|seek)(?!\s+no\b)\s+(?:\w+\s+){0,3}?"
        r"(?:authori[sz]ation|approval|permission|sign-?off|go-?ahead|confirmation)\b", re.I)),
    ("get-confirmed", re.compile(r"\b(?:get|have)\s+(?:it|this|that|them|the plan)\s+confirmed\b", re.I)),
    ("ask-first", re.compile(
        r"(?<!not )(?<!n't )(?<!never )\bask\s+(?:david|the user|the human|the operator|a human|first|before)\b", re.I)),
    ("diagnosis-only", re.compile(
        r"\b(?:diagnos(?:is|e|tic)|investigat(?:ion|e))[- ]only\b"
        r"|\bread[- ]only\s+(?:scope|mode|authority|lane|work|investigation|task|card|role)\b", re.I)),
    ("report-only", re.compile(r"\breport[- ]only\b", re.I)),
    ("report-instead", re.compile(
        r"\b(?:report|describe|note|flag|surface|list)\b[^.!?]{0,80}?\binstead\b(?!\s+of\b)"
        r"|\binstead of (?:fixing|acting|applying|repairing|changing|restarting|deploying|merging)\b", re.I)),
]
_PROHIBITION = re.compile(
    rf"(?:^|[;:,(\u2014\u2013]\s*|\b(?:and|but|or|then|so)\s+(?=(?:never|do not|don't|must not)\b)|\*\*\s*)\b{_NEG}\b"
    rf"(?P<gap>(?:[\s,]+(?:\w+|`[^`]*`)){{0,4}}?)[\s,]+(?:ever\s+)?(?P<verb>{_ACT_VERBS})"
    r"(?:s|es|d|ed|ing|ped|ping|ted|ting|ned|ning|red|ring)?\b(?P<obj>[^;.!?]{0,60})", re.I)

_SCOPE = re.compile(
    r"\b(?:when|whenever|if|unless|while|during|until|once|where|wherever|except|only\s+(?:for|on|in|when|if|with|after))\b"
    r"|\b(?:for|in|on|during|within)\s+(?:a|an|the|this|that|any|every|such)?\s*(?:[\w/-]+\s+){0,2}?"
    r"(?:cards?|tasks?|runs?|jobs?|investigations?|incidents?|reviews?|sessions?|modes?|repos?|hosts?|PRs?|"
    r"branch(?:es)?|builds?|work|audits?|outages?|lanes?|profiles?)\b"
    # purpose-scoped anti-workaround rules: "never X merely to evade the gate", "... to dodge the quota"
    r"|\b(?:merely|just|solely|only)\s+(?:to|because|so)\b|\bas a workaround\b"
    r"|\bto (?:dodge|evade|bypass|avoid|work around|get around|clear|satisfy|skip|game)\b"
    r"|\bon (?:that|this|such) [\w-]+\b"
    # a sentence-leading context phrase: "In the user's real window, ...", "For runner investigations, ..."
    r"|^[\W_]*(?:in|on|for|during|within|inside|with|under)\b[^,;:]{3,120},",
    re.I)
# A "condition" that is really an authorization requirement is not a scope: "unless David approves", "until the
# user confirms", "if not authorized". "if an approval prompt is withdrawn" IS a scope.
_AUTH_CONDITION = re.compile(
    r"^(?:unless|until|if|once|when|after|only\s+(?:if|when|after))\s+(?:not\s+)?(?:[\w'-]+\s+){0,3}?"
    r"(?:approv(?:es|ed|al is given)|authori[sz](?:es|ed|ation is given)|confirm(?:s|ed)|says? (?:so|yes|ok|go)|"
    r"asks?|asked|permit(?:s|ted)|gives? (?:the )?go-?ahead|signs? off|grants? (?:it|permission))\b",
    re.I)

_FENCE = re.compile(r"^\s*(```|~~~)")
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*(\[\"'])")
_MD_PREFIX = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+|#+\s+|>\s*)+")


def sentences(text: str) -> list[str]:
    """Prose sentences of markdown text: fenced code dropped, list/heading markers stripped, one sentence per
    split on terminal punctuation. Line breaks inside a paragraph are joined so wrapped lines stay one sentence."""
    out: list[str] = []
    in_fence = False
    para: list[str] = []

    def flush() -> None:
        if para:
            joined = " ".join(para)
            out.extend(s.strip() for s in _SPLIT.split(joined) if s.strip())
            para.clear()

    for line in (text or "").splitlines():
        if _FENCE.match(line):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line.strip() or line.lstrip().startswith("|"):
            flush()
            continue
        if _MD_PREFIX.match(line):  # a new bullet / heading / numbered item starts a new sentence group
            flush()
            line = _MD_PREFIX.sub("", line)
        para.append(line.strip())
    flush()
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def added_sentences(old: str, new: str) -> list[str]:
    before = {_norm(s) for s in sentences(old)}
    return [s for s in sentences(new) if _norm(s) not in before]


def _has_scope(sentence: str) -> bool:
    for m in _SCOPE.finditer(sentence):
        if _AUTH_CONDITION.match(sentence[m.start():m.start() + 120]):
            continue  # an authorization, not a scope
        return True
    return False

_QUOTED = re.compile(r"\"[^\"]*\"|\u201c[^\u201d]*\u201d")


def classify_sentence(sentence: str) -> list[str]:
    """Labels of the unscoped don't-act markers in one sentence ([] = passes). Quoted text ("don't post" as an
    example of what a user says) is not a rule and is ignored."""
    sentence = _QUOTED.sub('""', sentence)
    labels = [label for label, pat in _AUTH_PATTERNS if pat.search(sentence)]
    for m in _PROHIBITION.finditer(sentence):
        if "`" in (m.group("gap") or "") or "`" in (m.group("obj") or "")[:40]:
            continue  # names a code span: a technical pitfall, not a blanket don't-act rule
        labels.append(f"prohibition:{m.group('verb').lower()}")
        break
    if labels and _has_scope(sentence):
        return []
    return labels


def find_hits(old: str, new: str) -> list[dict]:
    """[{sentence, labels}] for every added sentence that is an unscoped don't-act rule."""
    hits = []
    for s in added_sentences(old, new):
        labels = classify_sentence(s)
        if labels:
            hits.append({"sentence": s, "labels": labels})
    return hits


def hits_for_texts(pairs: Iterable[tuple[str, str]]) -> list[dict]:
    out: list[dict] = []
    for old, new in pairs:
        out.extend(find_hits(old, new))
    return out

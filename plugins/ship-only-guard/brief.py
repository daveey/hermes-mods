"""Refuse briefs and steers that order a fresh review for every new head of a state PR.

The guard (guard.py) carries an earlier exact-head approval across update-branch
merges of `state` when the PR's verbatim patch-id is unchanged, no blocking
verdict came later and the head rollup is green. A card body or comment that
says "any new head needs a fresh exact-head verdict" reads, to the worker, as a
stricter rule that overrides that carry: each base refresh then costs another
review round, which gives the base time to drift again (six extra rounds on one
PR). The check fires where the rule is written, not at merge time.

It refuses only an unconditional mandate about a state PR or a base refresh.
A mandate conditioned on the patch (patch-id, range-diff, a conflict) passes,
and a deliberate exception passes with a `no-carry: <reason>` marker.
"""
import re

_TOOLS = {"kanban_create": ("title", "body"), "kanban_comment": ("body",)}

# "Any new head needs a fresh exact-head verdict", "every changed head requires fresh ... review"
_EVERY_HEAD = re.compile(
    r"\b(?:any|every|each)\s+(?:new|changed|further|later|updated)\s+(?:pr\s+)?head\s+"
    r"(?:still\s+|also\s+)?(?:needs|requires|must\s+(?:get|have|obtain))\b[^.\n]{0,60}?\bfresh\b",
    re.I)
# "required fresh exact-head review", "obtain a fresh mandatory Opus exact-head verdict"
_FRESH_EXACT = re.compile(
    r"\b(?:requires?|required|needs?|obtain|get)\s+(?:a\s+)?fresh\s+(?:[\w/.-]+\s+){0,3}?"
    r"exact-head\s+(?:delta\s+)?(?:review|verdict|approval)",
    re.I)
# The text is about a PR to `state` or about a base-only refresh of a PR.
_STATE_OR_REFRESH = re.compile(
    r"\bstate\s+pr\b|\bpr\s+(?:to|against|into|on)\s+`?state`?|\bbranch\s+`?state`?|state-pr-landing"
    r"|--base\s+state\b|update-branch|\bbase[- ]drift\b|\bbranch\s+refresh|\brefresh(?:ed)?\s+(?:from|with)\s+`?state",
    re.I)
# A mandate conditioned on the PR's own patch is the carry rule, not a contradiction of it.
_CONDITION = re.compile(
    r"patch-?id|range-diff|patch\s+(?:changes|changed|differs|differ|is\s+different)|own[- ]diff"
    r"|\bconflict|no-carry:",
    re.I)

MESSAGE = (
    "[ship-only-guard] Refused: this text orders a fresh review for every new head of a state PR, "
    "with no patch condition. After a base-only refresh (update-branch merging `state`), "
    "ship-only-guard carries the earlier exact-head approval itself when the PR's "
    "`git patch-id --verbatim` is unchanged, no blocking verdict came later and the head rollup is "
    "green (kanban-worker references/state-pr-landing.md, \"State moved under the PR's CI\"). A "
    "worker reads your stricter rule as overriding that carry, and each extra review round lets the "
    "base drift again. Write the condition instead, e.g. \"after an update-branch, rerun the "
    "head-pinned merge; file a fresh exact-head review only if the verbatim patch-id differs or the "
    "refresh resolved a conflict\". If a review of every head is really required, say why with a "
    "`no-carry: <reason>` line and resend.")


def _text(tool, args):
    if not isinstance(args, dict):
        return ""
    return "\n".join(v for k in _TOOLS.get(tool, ()) for v in [args.get(k)] if isinstance(v, str))


def check(tool_name, args):
    """Return a refusal message, or None to allow."""
    tool = (tool_name or "").rsplit("__", 1)[-1]
    text = _text(tool, args)
    if not text or not _STATE_OR_REFRESH.search(text) or _CONDITION.search(text):
        return None
    if _EVERY_HEAD.search(text) or _FRESH_EXACT.search(text):
        return MESSAGE
    return None

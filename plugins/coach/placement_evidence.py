"""Placement hygiene shared by every card this plugin (and the plugins that borrow it) files.

Core's placement check (``hermes_cli.kanban_placement.portable_reason``) scans a card's title and body for words that
only make sense on the gateway that owns them (a state path, ``kanban.db``, ``ssh``, ``cron``, a Desktop app...). A
producer that pastes the incident it observed (a log excerpt, a path, a "seen on <host>" section) into the body trips
those words, so a repo fix that any worker could land stays on the origin gateway. Two rules keep that honest:

* Evidence is fenced: :func:`fence_evidence` wraps pasted incident text in ``<!-- placement:evidence -->`` ...
  ``<!-- /placement:evidence -->``, which the scan skips (complete pairs only). Real instructions stay outside it.
* A card that really is home-bound says so: :func:`home_pin_note` is the one-sentence reason in its body and
  :data:`HOME_REQUIRES` the explicit ``host_repo`` capability (the CLI's pin; the tool path uses ``placement="local"``).

Titles never carry the host or product only to say where something was observed (``... on <host>``); the host belongs in
the fenced evidence. A CI job name in a title is quoted in backticks.

Producers import this by path (``from .placement_evidence import ...``, else load the sibling file): coach modules also
run as scripts and are loaded by spec in tests, so a plain ``import placement_evidence`` is not always on ``sys.path``.
"""
from __future__ import annotations

import re

EVIDENCE_OPEN = "<!-- placement:evidence -->"
EVIDENCE_CLOSE = "<!-- /placement:evidence -->"
# Same strings as core's kanban_placement.EVIDENCE_OPEN/CLOSE (a test compares them with the real core).
_MARKER = re.compile(r"<!--\s*/?\s*placement:evidence\s*-->", re.IGNORECASE)

# The CLI's explicit "this card stays on the gateway that filed it" pin: core treats host_repo as uncarried by workers.
HOME_REQUIRES = ("terminal", "file", "git", "host_repo")
HOME_REQUIRES_ARGV = tuple(x for c in HOME_REQUIRES for x in ("--requires", c))  # for `hermes kanban create`
HOME_PIN_PREFIX = "Pinned to the gateway that filed it (`host_repo`):"


def strip_markers(text: str) -> str:
    """``text`` with every fence marker removed, including ones a removal would re-form from nested halves
    (``<!-- placement:<!-- placement:evidence -->evidence -->``): an unsanitized marker in pasted evidence would end
    the fence early, or open a fake one that hides real instructions."""
    text = text or ""
    while True:
        cleaned = _MARKER.sub("", text)
        if cleaned == text:
            return cleaned
        text = cleaned


def fence_evidence(text: str) -> str:
    """``text`` inside one placement evidence fence, markers in it removed (see :func:`strip_markers`)."""
    return f"{EVIDENCE_OPEN}\n{strip_markers(text).strip(chr(10))}\n{EVIDENCE_CLOSE}"


def home_pin_note(reason: str) -> str:
    """The one-sentence reason a home-bound card stays on its own gateway. It is instructions, so it is never fenced."""
    reason = " ".join((reason or "").split()).rstrip(".")
    return f"{HOME_PIN_PREFIX} {reason}."

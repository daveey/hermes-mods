"""Placement hygiene shared by every card this plugin (and the plugins that borrow it) files.

Core's placement check (``hermes_cli.kanban_placement.portable_reason``) scans a card's title and body for words that
only make sense on the gateway that owns them (a state path, ``kanban.db``, ``ssh``, ``cron``, a Desktop app...). A
producer that pastes the incident it observed (a log excerpt, a path, a "seen on <host>" section) into the body trips
those words, so a repo fix that any worker could land stays on the origin gateway. Two rules keep that honest:

* Evidence is fenced: :func:`fence_evidence` wraps pasted incident text in ``<!-- placement:evidence -->`` ...
  ``<!-- /placement:evidence -->``, which the scan skips (complete pairs only). Real instructions stay outside it.
* A card that really is home-bound says so: :func:`home_pin_note` is the one-sentence reason in its body and
  :func:`home_runs_on_argv` the explicit ``--runs-on machine:<this gateway>`` pin (the tool path uses
  ``placement="local"``). ``host_repo`` is a capability a worker may or may not carry, NOT a machine pin, so it is
  never used to say "stay here"; :data:`HOME_REQUIRES` holds only the genuine tool capabilities.

Titles never carry the host or product only to say where something was observed (``... on <host>``); the host belongs in
the fenced evidence. A CI job name in a title is quoted in backticks.

Producers import this by path (``from .placement_evidence import ...``, else load the sibling file): coach modules also
run as scripts and are loaded by spec in tests, so a plain ``import placement_evidence`` is not always on ``sys.path``.
"""
from __future__ import annotations

import re
import hashlib

EVIDENCE_OPEN = "<!-- placement:evidence -->"
EVIDENCE_CLOSE = "<!-- /placement:evidence -->"
# Same strings as core's kanban_placement.EVIDENCE_OPEN/CLOSE (a test compares them with the real core).
_MARKER = re.compile(r"<!--\s*/?\s*placement:evidence\s*-->", re.IGNORECASE)

# Genuine capabilities a home-bound card's worker needs (NOT a placement pin).
HOME_REQUIRES = ("terminal", "file", "git")
HOME_REQUIRES_ARGV = tuple(x for c in HOME_REQUIRES for x in ("--requires", c))  # for `hermes kanban create`
HOME_PIN_PREFIX = "Pinned to the gateway that filed it:"


class HomePinUnresolved(RuntimeError):
    """This host's Board is synced (or may be) but the configured gateway identity could not be read: the card must
    not be filed, since an unpinned home-bound card would silently become portable."""


def _core_identity():
    """``(synced, gateway)`` from core's own config/enrollment (``kanban.sync`` + hub identity). Raises on failure."""
    from hermes_cli import kanban_db_sync, kanban_sync_client
    if not kanban_db_sync.sync_enabled():
        return False, None
    return True, kanban_sync_client.load_identity().gateway


def home_machine():
    """The configured Board gateway name when this host's board is synced, else None (unsynced: nothing to pin to).
    Raises :class:`HomePinUnresolved` when the identity cannot be read."""
    try:
        synced, gateway = _core_identity()
    except Exception as exc:  # health: allow BLE001 -- fail closed, never silently portable
        raise HomePinUnresolved(f"cannot resolve this gateway's Board identity ({type(exc).__name__}: {exc})") from exc
    if not synced:
        return None
    if not gateway:
        raise HomePinUnresolved("kanban sync is enabled but no gateway name is configured")
    return str(gateway)


def home_runs_on_argv() -> list:
    """``["--runs-on", "machine:<gateway>"]`` for a synced board; ``[]`` for an unsynced one (single-host board: the
    card already stays here). Raises :class:`HomePinUnresolved` on a synced-discovery failure."""
    machine = home_machine()
    return ["--runs-on", f"machine:{machine}"] if machine else []


def home_pin_argv() -> list:
    """Capabilities plus the dynamic machine pin, for `hermes kanban create`."""
    return [*HOME_REQUIRES_ARGV, *home_runs_on_argv()]


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


def incident_title(text: str | None) -> str:
    """An opaque label for raw symptoms; retain the full symptom in fenced body evidence.

    Do not truncate or blacklist raw text into titles: arbitrary host names and split fence markers can survive
    either approach. A digest is stable for dedup/readability without turning evidence into placement instructions.
    """
    return "incident " + hashlib.sha256((text or "defect").encode()).hexdigest()[:12]

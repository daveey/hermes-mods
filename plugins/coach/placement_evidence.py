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


# The Board identity the last core-visible process on this host read (t_01a1f71e279b7d22). A producer that runs
# under a bare interpreter (the hub's `ssh hub "python3 self_heal.py file"` for a Mac's or the mini's defect) cannot
# import core, but the card it files is created by the `hermes` CLI, which IS synced. Answering "unsynced" there
# put the hub's bb defects on the named bb board with no pool labels, and core's hub admission refused them.
IDENTITY_CACHE = ("cache", "board-identity.json")
IDENTITY_CACHE_MAX_AGE_S = 24 * 3600  # the hub lane cron refreshes it every 5 min
IDENTITY_CACHE_REWRITE_S = 3600


def _identity_cache_path(root=None):
    import os
    from pathlib import Path
    if root is None:
        home = os.environ.get("HERMES_HOME", "").strip()
        root = Path(home).expanduser() if home else Path.home() / ".hermes"
        if root.parent.name == "profiles":  # a profile home: the Board identity is the root's
            root = root.parent.parent
    return Path(root).joinpath(*IDENTITY_CACHE)


def _record_identity(gateway, now=None):
    """Remember the synced Board gateway core reported, for this host's core-less producers. Fail-open."""
    import json
    import os
    import socket
    import time
    if not gateway:
        return
    try:
        from hermes_constants import get_default_hermes_root
        path = _identity_cache_path(get_default_hermes_root())
        now = time.time() if now is None else now
        rec = {"synced": True, "gateway": str(gateway), "host": socket.gethostname()}
        try:
            old = json.loads(path.read_text())
            if {k: old.get(k) for k in rec} == rec and now - float(old.get("at") or 0) < IDENTITY_CACHE_REWRITE_S:
                return
        except Exception:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({**rec, "at": now}))
        os.replace(tmp, path)
    except Exception:  # health: allow BLE001 -- the cache is an optimisation for core-less processes only
        pass


def _cached_identity(now=None):
    """``(synced, gateway)`` a core-visible process on THIS host recorded within the max age, else None."""
    import json
    import socket
    import time
    try:
        rec = json.loads(_identity_cache_path().read_text())
        now = time.time() if now is None else now
        if rec.get("host") != socket.gethostname() or now - float(rec.get("at") or 0) > IDENTITY_CACHE_MAX_AGE_S:
            return None
        if rec.get("synced") is not True or not rec.get("gateway"):
            return None
        return True, str(rec["gateway"])
    except Exception:  # health: allow BLE001 -- absent/unreadable cache: the core-free answer below
        return None


def _core_identity():
    """``(synced, gateway)`` from core's own config/enrollment (``kanban.sync`` + hub identity). Raises on failure.

    No Hermes core importable in this process (a bare interpreter): the identity a core-visible process on this host
    recorded (:func:`_cached_identity`), since the card is still created by this host's `hermes` CLI; with none
    (core-free CI) no Board sync client exists, so the card can only land on the local board: ``(False, None)``.
    A core that IS installed but fails to report its identity still raises (fail closed)."""
    try:
        from hermes_cli import kanban_db_sync, kanban_sync_client
    except ModuleNotFoundError as exc:
        if exc.name != "hermes_cli":
            raise
        return _cached_identity() or (False, None)
    if not kanban_db_sync.sync_enabled():
        # Not recorded: an unreadable config also reads as off here, and a core-less process with no record already
        # answers unsynced. Only a positive identity is worth handing on.
        return False, None
    gateway = kanban_sync_client.load_identity().gateway
    _record_identity(gateway)
    return True, gateway


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


# ── the pool route for a home-bound card on a remote-reachable gateway ──────────────────────────────────────────────
# Core's hub admission (hermes_cli/kanban_hub_admission_placement.apply) refuses an explicit `machine:hub` pin unless
# a judge admits an allowlisted hub-only need (skill hub-admission-test). A home-bound coach card's gateway steps are
# reads (`ssh hermes-hub`, hub-read) and routine writes (`hub-admin '<command>'`), which pool workers carry, so on the
# hub such a card is filed for the pool: ``--runs-on`` the worker attestations below (not hub-only, so core makes no
# judge call) and a body whose pin sentence becomes :data:`REMOTE_NOTE`. Every other gateway keeps the machine pin
# (a Mac's state has no remote route). t_01a1cfa3b3805315 (self-heal), t_01a1cfb30dc5d3da (the other producers).
REMOTE_GATEWAYS = ("hub",)  # Board gateways whose state a pool worker reaches with hub-read and hub-admin
# Board labels a worker publishes only after a real proof of each tool (kanban-hub-pin-audit): hub reads, hub-admin
# writes, and reading other cards' handoffs. The hub itself carries none of them, so the card leaves it.
POOL_LABELS = ("capability:hub-read", "capability:hub-write", "capability:coordination-read")
REMOTE_NOTE = ("This card runs on a pool worker. The state it works on (logs, state files, configs, processes and "
               "databases under `~/.hermes`) lives on the gateway that filed it: read it there with "
               "`ssh hermes-hub <cat|tail|head|grep|ls|sqlite ...>` (`ssh hermes-hub help` lists them) or "
               "`hub-admin '<command>'`, and run every command that inspects processes or changes that state "
               "(including `hermes ...` and the `python3 ~/code/hermes-mods/...` rechecks below) as "
               "`hub-admin '<command>'` (single-quoted, so `$HOME` and `~` expand on the gateway). Run locally they "
               "would read or change this worker's own `~/.hermes`.")
_PIN_LINE_RE = re.compile(re.escape(HOME_PIN_PREFIX) + r"[^\n]*")


def remote_home() -> bool:
    """True when this gateway's home-bound cards go to the pool (:data:`REMOTE_GATEWAYS`). An unresolvable identity
    is False: the home-pin path then fails the create closed (:class:`HomePinUnresolved`)."""
    try:
        return home_machine() in REMOTE_GATEWAYS
    except HomePinUnresolved:
        return False


def remote_body(body: str, note: str = REMOTE_NOTE) -> str:
    """``body`` for a pool worker: the home-pin sentence (:func:`home_pin_note`) becomes ``note``."""
    return _PIN_LINE_RE.sub(lambda _m: note, body or "")


def pool_runs_on_argv() -> list:
    return ["--runs-on", ",".join(POOL_LABELS)]


def home_route(body: str, note: str = REMOTE_NOTE) -> tuple:
    """``(argv, body)`` for a home-bound `hermes kanban create`: on a remote-reachable gateway the pool labels and
    the remote brief, else the capabilities plus the machine pin (:func:`home_pin_argv`) and ``body`` unchanged.
    Raises :class:`HomePinUnresolved` like :func:`home_pin_argv`."""
    if remote_home():
        return [*HOME_REQUIRES_ARGV, *pool_runs_on_argv()], remote_body(body, note)
    return home_pin_argv(), body


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

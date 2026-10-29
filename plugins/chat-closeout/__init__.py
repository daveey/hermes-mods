"""chat-closeout: a chat that hands work to Kanban cards always shows what it waits on, and closes itself out when
they finish. Logic lives in core.py; this file wires Hermes hooks and the backend thread.

  post_llm_call     waiting ledger: stamp the chat's open cards at every turn end (and close a settled batch)
  pre_llm_call      a turn that runs after the whole batch settled gets the close-out brief (handoffs + what to say)
  pre_final_gate    a reply promising to report later with nothing tracked behind it is held (then annotated)
  thread (serve)    owner wake (owner_wake.py): a card parked for an agent-owned reason starts one supervise turn in
                    its owning chat (archived/reaped/gone chat -> board-supervisor card), once per card per premise
  thread (serve)    close-out waker: wake an idle chat whose cards all settled, once per batch state (in-process
                    resume via server._wake_session_turn), at most MAX_WAKES_PER_HOUR per chat; watchdog every 15 min: Kanban card + WARNING for a chat
                    with unseen kanban results > 30 min or a settled batch with no close-out > 30 min
Every hook fails open. [daveey]
"""
from __future__ import annotations

import contextlib
import importlib.util
import logging
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

if __package__:
    from . import core
    from . import owner_wake
    from . import routing
else:  # pytest imports the hyphenated plugin dir without a parent package
    _spec = importlib.util.spec_from_file_location("chat_closeout_core", Path(__file__).with_name("core.py"))
    core = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(core)
    core = sys.modules.setdefault("chat_closeout_core", core)
    _spec = importlib.util.spec_from_file_location("chat_closeout_owner_wake", Path(__file__).with_name("owner_wake.py"))
    owner_wake = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(owner_wake)
    owner_wake = sys.modules.setdefault("chat_closeout_owner_wake", owner_wake)
    _spec = importlib.util.spec_from_file_location("chat_closeout_routing", Path(__file__).with_name("routing.py"))
    routing = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(routing)
    routing = sys.modules.setdefault("chat_closeout_routing", routing)

logger = logging.getLogger("chat_closeout")
SKIP_PLATFORMS = {"cron", "kanban", "webhook", "subagent"}
TICK_S = float(os.environ.get("CHAT_CLOSEOUT_TICK_S", "30"))
_state: dict[str, Any] = {}
_thread_lock = threading.Lock()
_waiting_lock = threading.Lock()


# ---- environment -----------------------------------------------------------------------------------------------
def _off() -> bool:
    return bool(os.environ.get("CHAT_CLOSEOUT_OFF"))


def _kanban_root() -> Path:
    try:
        from hermes_cli import kanban_db
        return Path(kanban_db.kanban_home())
    except Exception:
        return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def ledger() -> "core.Ledger":
    path = os.environ.get("CHAT_CLOSEOUT_DB") or str(_kanban_root() / "cache" / "chat-closeout" / "ledger.db")
    if _state.get("ledger_path") != path:
        _state.update(ledger_path=path, ledger=core.Ledger(Path(path)))
    return _state["ledger"]


def board_paths() -> list[Path]:
    try:
        from hermes_cli import kanban_session_activity as ksa
        return list(ksa._board_db_paths())
    except Exception:
        root = _kanban_root()
        return [p for p in [root / "kanban.db", *sorted((root / "kanban" / "boards").glob("*/kanban.db"))]
                if p.is_file()]


def remote_cards() -> list[dict]:
    """Use core's authoritative projection, including released rows (session_tasks omits those).

    No replica/status-only fallback can prove a release. Older cores retain unknown
    remote batches rather than silently treating a missing card as completed.
    """
    from hermes_constants import get_hermes_home
    from hermes_cli import kanban_session_activity as ksa
    from hermes_cli.kanban_origin_adapter import _gateway
    from hermes_cli.kanban_origin_ledger import project
    from hermes_cli.profiles import get_active_profile_name

    path = get_hermes_home() / 'kanban' / 'origin-ledger.db'
    if not path.exists():
        return ksa._remote_links()
    shared = getattr(ksa, "remote_links_and_origin_rows", None)
    if shared is not None:
        # Newer cores keep the ledger rows of their cached projection: same rows, no second project() per pass.
        cards, rows = shared()
        return [card for card in cards if card.get('via') != 'origin'] + rows
    replica = ksa._hub_db_path()
    rows = project(path, replica=replica,
                   gateway=_gateway(replica or path.with_name('absent-replica.db')),
                   profile=get_active_profile_name())
    # Core alone decides which historical associations the receipts supersede.
    # Its inventory also retains unsuperseded legacy obligations; don't lose those
    # when adding released rows (which the open inventory deliberately omits).
    return [card for card in ksa._remote_links() if card.get('via') != 'origin'] + rows


def read_cards(ids: list[str], remote_inventory: Optional[Callable[[], list[dict]]] = None) -> dict:
    cards = core.read_cards(board_paths(), ids)
    try:
        remote = (remote_inventory or remote_cards)()
    except Exception:
        # Qualified local cards remain usable. Old bare IDs have lost ownership:
        # without the remote inventory they cannot safely resolve to local done.
        logger.debug('chat-closeout: origin projection unavailable', exc_info=True)
        return {key: card for key, card in cards.items() if '/' in key}
    want = set(ids)
    for card in remote:
        key = core.card_key(card)
        cards.pop(str(card['id']), None)  # ambiguous historical bare-ID ledger
        if key in want:
            previous = cards.get(key)
            # Several origin bindings can name one owner; no unsafe first match.
            if previous is None or not core.is_settled(card):
                cards[key] = card
    return cards


def _pass_reader() -> Callable[[list[str]], dict]:
    """``read_cards`` for one pass over the ledger: the remote inventory (a full hub-replica projection) and the
    local boards are read once and reused for every row, not once per pending row. A failed read fails every row
    the same way."""
    memo: list[tuple[Optional[list[dict]], Optional[BaseException]]] = []

    def inventory() -> list[dict]:
        if not memo:
            try:
                memo.append((remote_cards(), None))
            except Exception as exc:
                memo.append((None, exc))
        rows, exc = memo[0]
        if exc is not None:
            raise exc
        return rows

    # Boards are read once per pass too: the first call reads every id the ledger's pending rows hold (one open per
    # board) and later rows take their subset. core.read_cards judges each id on its own, so the subset equals a
    # per-row read; ids outside the pass's union (a row written mid-pass) are read directly.
    union: dict[str, Any] = {}

    def read(ids: list[str]) -> dict:
        if "ids" not in union:
            try:
                union["ids"] = {i for row in ledger().pending() for i in row["batch"]}
            except Exception:
                union["ids"] = set()
        want = set(ids)
        if not want or not want <= union["ids"]:
            return read_cards(ids, remote_inventory=inventory)
        if "cards" not in union:
            union["cards"] = read_cards(sorted(union["ids"]), remote_inventory=inventory)
        return {key: card for key, card in union["cards"].items() if key in want}

    return read


# One waiting pass (``_proof_pass``): its connections, SessionDB and lineage lookups, reused by every proof in it.
_pass_local = threading.local()


@contextlib.contextmanager
def _proof_pass():
    """Share reads across one ``pending_closeouts`` pass: ~50 proofs each opened a SessionDB (for the compression
    tip) and a connection per state.db, ~45 ms of the sidebar's waiting source. Closed when the pass ends."""
    if getattr(_pass_local, "res", None) is not None:
        yield
        return
    res: dict[str, Any] = {"pool": core.ReadPool(), "tips": {}, "db": None}
    _pass_local.res = res
    try:
        yield
    finally:
        _pass_local.res = None
        res["pool"].close()
        if res["db"] is not None:
            res["db"].close()


def state_dbs() -> list[Path]:
    res = getattr(_pass_local, "res", None)
    if res is not None and "state_dbs" in res:
        return res["state_dbs"]
    root = _kanban_root()
    out = [root / "state.db"]
    out += sorted((root / "profiles").glob("*/state.db")) if (root / "profiles").is_dir() else []
    out = [p for p in out if p.is_file()]
    if res is not None:
        res["state_dbs"] = out
    return out


def open_card_ids(session_id: str) -> list[str]:
    """Cards the chat created or shepherds that are not done/archived (queued, running, blocked, review)."""
    from hermes_cli import kanban_session_activity as ksa
    return sorted({core.card_key(t) for t in ksa.session_tasks([session_id], ksa.LISTED_STATUSES)})


_ACTIVE_FALLBACK = ("todo", "ready", "running", "scheduled", "review")


def subscribed_open_cards(session_id: str, db_paths: Optional[list[Path]] = None) -> list[str]:
    """Working cards created elsewhere that notify this chat (``kanban_notify_subs``): a worker's follow-up card, or
    a card the chat commented on. Their completion notice arrives here as a new turn, so they back a promise just
    like a card the chat created. ``session_tasks`` only sees cards whose creator/origin is the chat, so without
    this a chat waiting on four subscribed cards was told it had none. Same link rule as ask-gate's
    ``linked_cards``; a board without the subs table (older core) contributes nothing."""
    try:
        from hermes_cli import kanban_session_activity as ksa
        active = tuple(ksa.ACTIVE_STATUSES)
    except Exception:
        active = _ACTIVE_FALLBACK
    ids = [i for i in _lineage(session_id) if i]
    if not ids:
        return []
    found: set[str] = set()
    for path in db_paths if db_paths is not None else board_paths():
        try:
            conn = core.ro_connect(Path(path))
        except sqlite3.Error:
            continue
        try:
            rows = conn.execute(
                "SELECT DISTINCT t.id FROM tasks t JOIN kanban_notify_subs s ON s.task_id = t.id "
                f"WHERE s.chat_id IN ({','.join('?' for _ in ids)}) "
                f"AND t.status IN ({','.join('?' for _ in active)})", (*ids, *active)).fetchall()
            found.update(str(r[0]) for r in rows)
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return sorted(found)


def session_proof(sid: str, tid: str, card: dict) -> bool:
    """Transcript evidence that a finished card's final notice was delivered to the chat and answered."""
    try:
        at = card.get("completed_at")
        if at is None:
            return False
        res = getattr(_pass_local, "res", None)
        return core.ack_proven(state_dbs(), _lineage(sid), tid, float(at),
                               pool=res["pool"] if res is not None else None)
    except Exception:
        logger.debug("chat-closeout: ack proof unavailable", exc_info=True)
        return False


def _eligible(session_id: str, platform: str = "") -> bool:
    return bool(session_id) and not _off() and not os.environ.get("HERMES_KANBAN_TASK") \
        and (platform or "").lower() not in SKIP_PLATFORMS


# ---- hooks -----------------------------------------------------------------------------------------------------
def post_llm_call(session_id: str = "", platform: str = "", **_: Any) -> None:
    """Waiting ledger: every turn end stamps ``waiting_on`` (the chat's open cards)."""
    if not _eligible(session_id, platform):
        return
    try:
        observed = _state.setdefault("observed", {}).pop(session_id, None)
        asked = _state.setdefault("observed_ask", {}).pop(session_id, None)
        out = core.record_turn_end(ledger(), session_id, open_card_ids(session_id), read_cards,
                                   observed_sig=observed)
        if asked:
            row = ledger().get(session_id) or {}
            batch = set(row.get("batch") or [])
            ids = {t for t, _ in asked if t in batch}
            ledger().write(session_id, covered=set(row.get("covered") or []) | ids,
                           relayed=set(row.get("relayed") or []) | {p for t, p in asked if t in ids})
        if out.get("open") or out.get("closeout"):
            logger.info("chat-closeout: %s waiting_on=%s%s", session_id, out.get("open"),
                        " (close-out turn: covered %s)" % sorted(out.get("covered") or [])
                        if out.get("closeout") else "")
    except Exception:
        logger.debug("chat-closeout post_llm_call failed", exc_info=True)


def pre_llm_call(session_id: str = "", platform: str = "", user_message: Any = None, **_: Any) -> Optional[dict]:
    """Conditional: only when this chat's whole batch has settled and no close-out turn has run for it (the live
    path: a kanban notice turn in an open chat). No tokens otherwise."""
    if not _eligible(session_id, platform):
        return None
    try:
        _state.setdefault("observed", {}).pop(session_id, None)
        _state.setdefault("observed_ask", {}).pop(session_id, None)
        row = ledger().get(session_id)
        if not row or not row.get("batch"):
            return None
        cards = read_cards(row["batch"])
        fix = core.reconcile_done(row, cards, lambda t, c: session_proof(session_id, t, c))
        if fix:
            ledger().write(session_id, **fix)
            row = {**row, **fix}
            if not row["batch"]:
                return None
        key = core.pending_key(row, cards)
        if not key and row.get("closing") is None:
            cands = core.ask_candidates(row, cards) if core.ask_relay_key(row, cards) else []
            if cands:  # the user is here anyway: tell this turn what waits on them (covered at its end)
                _state.setdefault("observed_ask", {})[session_id] = cands
                return {"context": core.ask_relay_prompt(row, cards, [t for t, _ in cands])}
        if not key or row.get("closing") is not None:
            return None  # nothing new to report, or the waker already briefed this turn
        _state["observed"][session_id] = key
        ledger().write(session_id, closing=sorted(row["batch"]))
        return {"context": core.closeout_prompt(row, cards)}
    except Exception:
        logger.debug("chat-closeout pre_llm_call failed", exc_info=True)
        return None


def _backed(session_id: str) -> bool:
    try:
        if open_card_ids(session_id):
            from hermes_cli import kanban_session_activity as ksa
            if ksa.session_tasks([session_id], ksa.ACTIVE_STATUSES):
                return True
    except Exception:
        logger.debug("chat-closeout: card probe failed; letting the reply through", exc_info=True)
        return True  # every hook fails open: a broken probe never blocks a reply
    try:
        if subscribed_open_cards(session_id):
            return True
    except Exception:
        logger.debug("chat-closeout: subscription probe failed; letting the reply through", exc_info=True)
        return True
    try:
        from hermes_cli import process_session_activity as psa
        if psa.background_process_counts().get(session_id):
            return True
    except Exception:
        pass
    try:
        from hermes_cli import cron_session_activity as csa
        if session_id in csa.cron_owner_sessions():
            return True
    except Exception:
        pass
    return False


def pre_final_gate(session_id: str = "", platform: str = "", final_response: str = "",
                   turn_messages: list | None = None, attempt: int = 0, **_: Any) -> Optional[dict]:
    """Turn-end lint: a promise to report later must have a tracked thing behind it."""
    if not _eligible(session_id, platform) or not isinstance(final_response, str):
        return None
    try:
        promise = core.find_promise(final_response)
        if not promise or _backed(session_id):
            return None
        logger.info("chat-closeout: unbacked promise in %s (%r), attempt %d", session_id, promise, attempt)
        if attempt < 1:
            return {"hold": True, "nudge": core.LINT_NUDGE.format(tag=core.TAG, promise=promise)}
        return {"append": core.LINT_APPEND}
    except Exception:
        logger.debug("chat-closeout pre_final_gate failed", exc_info=True)
        return None


pre_final_gate.needs_buffer = True

# ---- sidebar: a settled batch stays "waiting" until its close-out ----------------------------------------------
def waiting_source() -> dict[str, list[str]]:
    """kanban_session_activity waiting source: chats whose cards all settled but whose close-out has not run yet.
    Cached for 3 s (the session list polls). Single-flight: polls that arrive while a pass runs wait for it and
    share its answer instead of each starting their own."""
    if _off():
        return {}
    with _waiting_lock:
        hit = _state.get("waiting_cache")
        if hit and time.monotonic() - hit[0] < 3.0:
            return hit[1]
        try:
            with _proof_pass():
                out = core.pending_closeouts(ledger(), _pass_reader(), proof=session_proof)
        except Exception:
            logger.debug("chat-closeout waiting source failed", exc_info=True)
            out = {}
        _state["waiting_cache"] = (time.monotonic(), out)
        return out


def _register_waiting_source() -> None:
    try:
        from hermes_cli import kanban_session_activity as ksa
    except Exception:
        return
    reg = getattr(ksa, "register_waiting_source", None)
    if reg is not None:  # older cores lack the seam: the row just goes green a little early
        reg("chat-closeout", waiting_source)


# ---- the backend thread: close-out waker + watchdog -------------------------------------------------------------
def _server():
    """The tui_gateway server module when this process is a Desktop/TUI backend that can wake chats, else None."""
    mod = sys.modules.get("tui_gateway.server")
    return mod if mod is not None and hasattr(mod, "_wake_session_turn") else None


def _lineage(sid: str) -> list[str]:
    ids = [sid]
    try:
        tip = _compression_tip(sid)
        if tip and tip not in ids:
            ids.append(tip)
    except Exception:
        pass
    return ids


def _unseen_counts() -> Optional[dict[str, int]]:
    """Unseen kanban results per session, or None when unreadable: a failed read must never look like "all
    notices delivered" (that would wake a chat before its results landed)."""
    try:
        from hermes_cli import kanban_session_activity as ksa
        return dict(ksa._all_counts()[2])
    except Exception:
        logger.debug("chat-closeout: unseen counts unavailable", exc_info=True)
        return None


def wake_tick(*, now: Optional[float] = None, wake: Optional[Callable[..., str]] = None,
              unseen: Optional[dict[str, int]] = None, last_user: Optional[Callable[[list[str]], float]] = None,
              read: Optional[Callable[[list[str]], dict]] = None,
              proof: Optional[Callable[[str, str, dict], bool]] = None) -> list[dict]:
    """One close-out pass over the ledger. Returns what it did (for logs/tests). One remote-inventory read per
    tick, shared by every pending row."""
    now = time.time() if now is None else now
    read = read or _pass_reader()
    if wake is None:
        srv = _server()
        if srv is None:
            return []
        wake = srv._wake_session_turn
    unseen = _unseen_counts() if unseen is None else unseen
    if unseen is None:
        return []
    last_user = last_user or (lambda ids: core.last_user_at(state_dbs(), ids))
    led = ledger()
    done: list[dict] = []
    for row in led.pending():
        sid = row["session_id"]
        if _session_source(sid).lower() in SKIP_PLATFORMS:
            # A worker/cron transcript is not a chat: it cannot be resumed (resume is refused), so a ledger row for
            # it (stamped by a turn that ran without HERMES_KANBAN_TASK) would retry and raise ask-failed forever.
            led.write(sid, batch=[], open=[], failure_since=None, retry_after=0)
            logger.info("chat-closeout: dropped ledger row of non-chat session %s", sid)
            continue
        cards = read(row["batch"])
        fix = core.reconcile(row, cards)
        fix.update(core.reconcile_done({**row, **fix}, cards,
                                       lambda t, c, sid=sid: (proof or session_proof)(sid, t, c)))
        if fix:
            led.write(sid, **fix)
            row = {**row, **fix}
            if not row["batch"]:
                continue
        key = core.pending_key(row, cards)
        if key and key != row.get("settled_sig"):
            led.write(sid, settled_sig=key, settled_at=now)
        ids = _lineage(sid)
        if key and core.wakes_last_hour(row, now) >= core.MAX_WAKES_PER_HOUR:
            if _state.setdefault("capped", {}).get(sid) != key:
                _state["capped"][sid] = key
                logger.warning("chat-closeout: %s hit the %d wakes/hour cap; close-out deferred", sid,
                               core.MAX_WAKES_PER_HOUR)
        n_unseen, user_at = sum(unseen.get(i, 0) for i in ids), last_user(ids)
        due = core.closeout_due(row, cards, unseen=n_unseen, last_user_at=user_at, now=now)
        if not due:
            relay = core.ask_relay_due(row, cards, unseen=n_unseen, last_user_at=user_at, now=now)
            if relay and led.claim(sid, relay, now):
                done.append(_relay(led, sid, row, cards, relay, now, wake))
            continue
        if not led.claim(sid, due, now):
            continue
        new_ids = core.uncovered(row, cards)
        # Mark the batch covered and the turn as a close-out BEFORE it starts: the turn's own end (or a card it
        # creates) must never read as a fresh settled batch.
        wakes = [t for t in row.get("wakes") or [] if 0 <= now - float(t) < 3600] + [now]
        led.write(sid, covered=set(row.get("covered") or []) | set(new_ids), closing=sorted(row["batch"]),
                  wakes=wakes, wake_count=int(row.get("wake_count") or 0) + 1)
        try:
            result = wake(sid, core.closeout_prompt(row, cards), display_kind="kanban_notice",
                          display_metadata={"display_text": core.notice_text(row, cards), "closeout": True,
                                            "task_ids": sorted(row["batch"])},
                          label="chat-closeout")
        except Exception:
            logger.warning("chat-closeout: wake failed for %s", sid, exc_info=True)
            result = "error"
        if result == "started":
            led.write(sid, wake_result="started", failure_since=None, retry_after=0)
            logger.info("chat-closeout: woke %s for its close-out (%d cards settled, %d new; wake #%d)", sid,
                        len(row["batch"]), len(new_ids), int(row.get("wake_count") or 0) + 1)
        else:
            # busy/refused/error: undo the coverage + count; a later tick (or the owning backend) retries
            led.write(sid, covered=set(row.get("covered") or []), closing=None, wakes=row.get("wakes") or [],
                      wake_count=int(row.get("wake_count") or 0))
            led.release(sid, due, result, now=now,
                        backoff=0 if result == "busy" else owner_wake.CLAIM_TTL_S)
            logger.info("chat-closeout: close-out wake of %s deferred (%s)", sid, result)
        done.append({"session_id": sid, "result": result, "sig": due})
    return done


def _session_scope(sid: str) -> tuple[Optional[Path], str]:
    for path in state_dbs():
        c = core.ro_connect(path)
        try:
            if c.execute("SELECT 1 FROM sessions WHERE id=?", (sid,)).fetchone():
                return path, path.parent.name if path.parent.parent.name == "profiles" else "default"
        finally:
            c.close()
    return None, "default"


def _session_source(sid: str) -> str:
    """state.db ``sessions.source`` of ``sid`` ('' when unknown)."""
    path, _ = _session_scope(sid)
    if path is None:
        return ""
    c = core.ro_connect(path)
    try:
        r = c.execute("SELECT source FROM sessions WHERE id=?", (sid,)).fetchone()
        return (r[0] or "") if r else ""
    except Exception:
        return ""
    finally:
        c.close()


def _moved_owner(sid: str) -> Optional[str]:
    path, _ = _session_scope(sid)
    return routing.moved_owner(sid, db_path=path)


def _handoff_ask(led, sid: str, gateway: str, key: str, prompt: str, now: float) -> str:
    _, profile = _session_scope(sid)
    return routing.handoff(led, sid, gateway, key, prompt, now, profile=profile)


def _relay(led, sid: str, row: dict, cards: dict, key: str, now: float, wake: Callable[..., str]) -> dict:
    """Ask relay: ONE turn telling the chat a batch card waits on the user while other batch cards still work. The
    asked cards are covered and their premises recorded BEFORE the turn (it is not a close-out: no ``closing``, the
    working cards keep their own close-out); a failed wake undoes both."""
    cands = core.ask_candidates(row, cards)
    ids = [t for t, _ in cands]
    batch = set(row.get("batch") or [])
    before = {k: row.get(k) for k in ("covered", "relayed", "wakes", "wake_count")}
    wakes = [t for t in row.get("wakes") or [] if 0 <= now - float(t) < 3600] + [now]
    relayed = {p for p in row.get("relayed") or [] if p.rsplit("@", 1)[0] in batch} | {p for _, p in cands}
    led.write(sid, covered=set(row.get("covered") or []) | set(ids), relayed=relayed, wakes=wakes,
              wake_count=int(row.get("wake_count") or 0) + 1)
    try:
        gateway = _moved_owner(sid)
        prompt = core.ask_relay_prompt(row, cards, ids)
        if gateway:
            result = _handoff_ask(led, sid, gateway, key, prompt, now)
        else:
            result = wake(sid, prompt, display_kind="kanban_notice",
                          display_metadata={"display_text": core.ask_notice_text(cards, ids), "ask_relay": True,
                                            "task_ids": ids}, label="chat-closeout")
    except Exception:
        logger.warning("chat-closeout: ask relay wake failed for %s", sid, exc_info=True)
        result = "error"
    if result in ("started", "forwarded"):
        led.write(sid, wake_result=result, failure_since=None, retry_after=0)
        logger.info("chat-closeout: relayed %d ask(s) to %s while %d card(s) still work (%s)", len(ids), sid,
                    sum(1 for t in batch if not core.is_settled(cards.get(t))), ", ".join(ids))
    else:
        led.write(sid, covered=set(before["covered"] or []), relayed=set(before["relayed"] or []),
                  wakes=before["wakes"] or [], wake_count=int(before["wake_count"] or 0))
        led.release(sid, key, result, now=now,
                    backoff=0 if result == "busy" else owner_wake.CLAIM_TTL_S)
        logger.info("chat-closeout: ask relay to %s deferred (%s)", sid, result)
    return {"session_id": sid, "result": result, "sig": key, "ask_relay": ids}


def wake_log() -> "owner_wake.WakeLog":
    path = os.environ.get("CHAT_CLOSEOUT_OWNER_WAKE_DB") or str(_kanban_root() / "cache" / "chat-closeout" / "owner-wake.db")
    if _state.get("wake_log_path") != path:
        _state.update(wake_log_path=path, wake_log=owner_wake.WakeLog(Path(path)))
    return _state["wake_log"]


def _compression_tip(sid: str) -> Optional[str]:
    """Read-only and closed. The sidebar's waiting pass asks once per finished card, and a writable SessionDB()
    runs schema init plus a system-wide open-file scan (~60 ms CPU) and was never closed: 23 of them per sidebar
    poll held the Desktop backend near 100% CPU (core/backend-writable-open-guard.md)."""
    from hermes_state import SessionDB
    res = getattr(_pass_local, "res", None)
    if res is not None:  # one read-only SessionDB and one lookup per session for the whole waiting pass
        if sid not in res["tips"]:
            if res["db"] is None:
                res["db"] = SessionDB(read_only=True)
            res["tips"][sid] = res["db"].get_compression_tip(sid)
        return res["tips"][sid]
    db = SessionDB(read_only=True)
    try:
        return db.get_compression_tip(sid)
    finally:
        db.close()


def owner_wake_tick(*, now: Optional[float] = None, cards: Optional[list[dict]] = None,
                    wake: Optional[Callable[..., str]] = None,
                    chat_info: Optional[Callable[[str], Optional[dict]]] = None,
                    supervise: Optional[Callable[[list[dict], str, str], str]] = None,
                    last_user: Optional[Callable[[str], float]] = None,
                    dependents: Optional[Callable[[dict], list[str]]] = None) -> list[dict]:
    """Owner wake (owner_wake.py): a card parked for an agent-owned reason starts ONE supervise turn in the chat that
    owns it (archived/reaped/gone chat -> a board-supervisor card). Needs core ``parked_waits``; older cores: no-op."""
    try:
        from hermes_cli import kanban_session_activity as ksa
    except ImportError:
        ksa = None
    if cards is None:
        if ksa is None or not hasattr(ksa, "parked_waits"):
            return []
        cards = ksa.parked_waits(now=now)
    if wake is None:
        srv = _server()
        wake = srv._wake_session_turn if srv is not None else None
    if dependents is None and ksa is not None and hasattr(ksa, "_board_db_paths"):
        kw: dict = {}
        if hasattr(ksa, "_open_ro"):
            kw["open_ro"] = ksa._open_ro  # survives a WAL db whose -wal/-shm were just removed
        try:
            from hermes_cli import kanban_wait_info as kwi
            kw["park_kinds"] = kwi.PARK_EVENT_KINDS
        except (ImportError, AttributeError):
            pass
        dependents = owner_wake.dependents_from(
            ksa._board_db_paths, getattr(ksa, "_hub_db_path", lambda: None), **kw)
    return owner_wake.tick(
        dependents=dependents,
        cards=cards, wake=wake, log=wake_log(),
        chat_info=chat_info or owner_wake.chat_info_from(state_dbs, _compression_tip),
        supervise=supervise or owner_wake.supervise_on_board,
        last_user=last_user or (lambda sid: core.last_user_at(state_dbs(), _lineage(sid))), now=now,
        canonical=_canonical_session)


def _canonical_session(sid: str) -> str:
    """The chat a session id belongs to now: its compression tip, else itself."""
    try:
        return _compression_tip(sid) or sid
    except Exception:
        return sid


MAX_POST_ATTEMPTS = 3  # failed card filings per incident key before one ERROR and no more retries


def file_card(title: str, body: str, key: str) -> str:
    """One Kanban card per stuck-chat incident on this gateway's default board (agent-fixable state belongs on the
    board, never the Inbox). ``key`` is the idempotency key, so a repeat of the same incident reuses the card."""
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing

    assignee = "default"
    try:
        from hermes_cli.config import load_config_readonly
        assignee = (load_config_readonly().get("kanban") or {}).get("orchestrator_profile") or "default"
    except Exception:
        pass
    text = (f"{body}\n\nchat-closeout watchdog incident `{key}`.\n\nFix: open the chat (resume it) and run its "
            "close-out turn, or find why the close-out waker / unseen-result sweeper did not deliver to it "
            "(gui.log, errors.log). Complete this card once the chat has reported its cards' results.")
    with connect_closing() as conn:
        return kb.create_task(conn, title=f"chat-closeout: {title}"[:200], body=text, assignee=assignee,
                              created_by="chat-closeout", idempotency_key=key)


def watchdog_tick(*, now: Optional[float] = None, mono: Optional[float] = None,
                  unseen: Optional[dict[str, int]] = None, post: Optional[Callable[[str, str, str], Any]] = None,
                  force: bool = False, read: Optional[Callable[[list[str]], dict]] = None) -> list[str]:
    """Flag stuck pipelines (once per chat+state): unseen kanban results > 30 min, or a settled batch with no
    close-out > 30 min. Conditions are observed every tick; flagging runs in one process per 15 min.

    Ages: the persisted wall-clock stamp must be > 30 min AND this process must have seen the condition for
    > 30 min of monotonic time, so a lid sleep (monotonic clock stops) or a restart never fakes a stale pipeline.
    The flag is recorded only after the Kanban card was filed, so a failed filing retries next run, up to
    MAX_POST_ATTEMPTS per key; then one ERROR and no more retries until the condition changes.
    Returns the flag keys raised. One remote-inventory read per tick, shared by every pending row."""
    post = post or file_card
    now = time.time() if now is None else now
    mono = time.monotonic() if mono is None else mono
    read = read or _pass_reader()
    led = ledger()
    unseen = _unseen_counts() if unseen is None else unseen
    if unseen is None:
        return []
    seen_mono: dict[str, float] = _state.setdefault("mono_seen", {})
    candidates: list[tuple[str, str, str]] = []
    for sid, since in led.unseen_since({s: n for s, n in unseen.items() if n > 0}, now).items():
        key = f"chat-closeout:unseen:{sid}:{int(since)}"
        first = seen_mono.setdefault(key, mono)
        if now - since < core.STALE_S or mono - first < core.STALE_S:
            continue
        msg = (f"Chat {sid} has had {unseen[sid]} kanban result(s) nobody handed to it for "
               f"{int((now - since) / 60)} min.")
        candidates.append((key, f"Kanban results stuck for chat {sid}", msg + " Open the chat (or check the "
                           "unloaded-session sweeper in gui.log) so the notices land."))
    for row in led.pending():
        sig, at = row.get("settled_sig"), row.get("settled_at")
        if not sig or not at or sig == row.get("done_sig"):
            continue
        if row.get("closing") is None and core.closeout_key(row, read(row["batch"])) != sig:
            continue  # reported since, or the batch moved on (a woken turn still in flight stays watched)
        key = f"chat-closeout:no-closeout:{row['session_id']}:{sig}"
        first = seen_mono.setdefault(key, mono)
        if now - float(at) < core.STALE_S or mono - first < core.STALE_S:
            continue
        msg = (f"Chat {row['session_id']}: its {len(row['batch'])} card(s) ({', '.join(row['batch'][:6])}) "
               f"settled {int((now - float(at)) / 60)} min ago and no close-out turn ran "
               f"(last wake: {row.get('wake_result') or 'none'}).")
        candidates.append((key, f"No close-out for chat {row['session_id']}", msg))
    for row in led.pending():
        since = row.get("failure_since")
        if since is None:
            continue
        sig = core.ask_relay_key(row, read(row["batch"]))
        if not sig:
            continue
        key = f"chat-closeout:ask-failed:{row['session_id']}:{int(since)}"
        first = seen_mono.setdefault(key, mono)
        if now - since >= core.STALE_S and mono - first >= core.STALE_S:
            candidates.append((key, f"Ask relay stuck for chat {row['session_id']}",
                               f"Chat {row['session_id']}: ask relay has not been delivered for "
                               f"{int((now-since)/60)} min (last result: {row.get('wake_result')})."))
    for handoff in routing.stalled(led, now, 0):
        key = f"chat-closeout:handoff:{handoff['id']}"
        first = seen_mono.setdefault(key, mono)
        if now - handoff['created_at'] < core.STALE_S or mono - first < core.STALE_S:
            continue
        candidates.append((key, f"Ask handoff unconfirmed for chat {handoff['session_id']}",
                           f"Courier request {handoff['id']} for chat {handoff['session_id']} has no "
                           "successful delivery receipt. Inspect the target before retrying; do not resend "
                           "an uncertain send automatically."))
    live = {k for k, _, _ in candidates}
    failures: dict[str, int] = _state.setdefault("post_failures", {})
    for k in [k for k in seen_mono if k not in live]:
        if not _still_observed(k, unseen, led):
            seen_mono.pop(k, None)
            failures.pop(k, None)
    candidates = [c for c in candidates if not led.has_flag(c[0]) and failures.get(c[0], 0) < MAX_POST_ATTEMPTS]
    if not candidates or (not force and not led.lease("watchdog", core.WATCHDOG_EVERY_S, now)):
        return []
    raised: list[str] = []
    for key, title, msg in candidates:
        logger.warning("chat-closeout watchdog: %s", msg)
        try:
            post(title, msg, key)
        except Exception:
            failures[key] = failures.get(key, 0) + 1
            if failures[key] >= MAX_POST_ATTEMPTS:
                logger.error("chat-closeout: filing the Kanban card for %s failed %d times; giving up until the "
                             "condition changes", key, failures[key], exc_info=True)
            else:
                logger.warning("chat-closeout: Kanban card filing failed for %s (retrying next run)", key,
                               exc_info=True)
            continue
        failures.pop(key, None)
        led.flag_once(key, now)
        raised.append(key)
    return raised


def _still_observed(key: str, unseen: dict[str, int], led) -> bool:
    """Whether a remembered watchdog condition still holds (else its monotonic first-seen is forgotten)."""
    parts = key.split(":")
    if len(parts) >= 3 and parts[1] == "unseen":
        return unseen.get(parts[2], 0) > 0
    if len(parts) >= 3 and parts[1] == "handoff":
        return any(r['id'] == parts[2] for r in routing.stalled(led, time.time(), 0))
    if len(parts) >= 4 and parts[1] == "ask-failed":
        row = led.get(parts[2]) or {}
        return row.get("failure_since") is not None and int(row["failure_since"]) == int(parts[3])
    if len(parts) >= 4 and parts[1] == "no-closeout":
        row = led.get(parts[2]) or {}
        return row.get("settled_sig") == parts[3] and row.get("done_sig") != parts[3]
    return False


def _loop(stop: threading.Event) -> None:
    stop.wait(float(os.environ.get("CHAT_CLOSEOUT_FIRST_S", "20")))
    while not stop.is_set():
        if _server() is not None and not _off():
            for fn in (wake_tick, owner_wake_tick, watchdog_tick):
                try:
                    fn()
                except Exception:
                    logger.warning("chat-closeout %s failed", fn.__name__, exc_info=True)
        stop.wait(TICK_S)


def _stop_stale_threads() -> int:
    """A plugin reload re-executes this module with a fresh ``_state``, but the previous module's waker thread keeps
    running the OLD wake logic next to the new one. Stop every earlier chat-closeout thread in the process via its
    stop event (its first arg)."""
    stopped = 0
    for t in threading.enumerate():
        if t.name != "chat-closeout" or t is _state.get("thread") or not t.is_alive():
            continue
        ev = (getattr(t, "_args", None) or (None,))[0]
        if isinstance(ev, threading.Event):
            ev.set()
            stopped += 1
    if stopped:
        logger.info("chat-closeout: stopped %d waker thread(s) from a previous load", stopped)
    return stopped


def start_thread() -> Optional[threading.Event]:
    with _thread_lock:
        if _state.get("thread") or TICK_S <= 0:
            return None
        _stop_stale_threads()
        stop = threading.Event()
        t = threading.Thread(target=_loop, args=(stop,), daemon=True, name="chat-closeout")
        _state.update(thread=t, stop=stop)
        t.start()
        return stop


def register(ctx) -> None:
    logger.info("chat-closeout: loaded relay-stamp-keep-v4 module=%s core=%s", __file__, core.__file__)
    ctx.register_hook("post_llm_call", post_llm_call)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("pre_final_gate", pre_final_gate)
    if not os.environ.get("HERMES_KANBAN_TASK"):
        _register_waiting_source()
        start_thread()

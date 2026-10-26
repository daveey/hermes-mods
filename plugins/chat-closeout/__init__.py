"""chat-closeout: a chat that hands work to Kanban cards always shows what it waits on, and closes itself out when
they finish. Logic lives in core.py; this file wires Hermes hooks and the backend thread.

  post_llm_call     waiting ledger: stamp the chat's open cards at every turn end (and close a settled batch)
  pre_llm_call      a turn that runs after the whole batch settled gets the close-out brief (handoffs + what to say)
  pre_final_gate    a reply promising to report later with nothing tracked behind it is held (then annotated)
  thread (serve)    close-out waker: wake an idle chat whose cards all settled, once per batch state (in-process
                    resume via server._wake_session_turn), at most MAX_WAKES_PER_HOUR per chat; watchdog every 15 min: Inbox item + WARNING for a chat
                    with unseen kanban results > 30 min or a settled batch with no close-out > 30 min
Every hook fails open. [daveey]
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

if __package__:
    from . import core
else:  # pytest imports the hyphenated plugin dir without a parent package
    _spec = importlib.util.spec_from_file_location("chat_closeout_core", Path(__file__).with_name("core.py"))
    core = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(core)
    core = sys.modules.setdefault("chat_closeout_core", core)

logger = logging.getLogger("chat_closeout")
SKIP_PLATFORMS = {"cron", "kanban", "webhook", "subagent"}
TICK_S = float(os.environ.get("CHAT_CLOSEOUT_TICK_S", "30"))
_state: dict[str, Any] = {}
_thread_lock = threading.Lock()


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
    replica = ksa._hub_db_path()
    rows = project(path, replica=replica,
                   gateway=_gateway(replica or path.with_name('absent-replica.db')),
                   profile=get_active_profile_name())
    # Core alone decides which historical associations the receipts supersede.
    # Its inventory also retains unsuperseded legacy obligations; don't lose those
    # when adding released rows (which the open inventory deliberately omits).
    return [card for card in ksa._remote_links() if card.get('via') != 'origin'] + rows


def read_cards(ids: list[str]) -> dict:
    cards = core.read_cards(board_paths(), ids)
    try:
        remote = remote_cards()
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


def state_dbs() -> list[Path]:
    root = _kanban_root()
    out = [root / "state.db"]
    out += sorted((root / "profiles").glob("*/state.db")) if (root / "profiles").is_dir() else []
    return [p for p in out if p.is_file()]


def open_card_ids(session_id: str) -> list[str]:
    """Cards the chat created or shepherds that are not done/archived (queued, running, blocked, review)."""
    from hermes_cli import kanban_session_activity as ksa
    return sorted({core.card_key(t) for t in ksa.session_tasks([session_id], ksa.LISTED_STATUSES)})


def session_proof(sid: str, tid: str, card: dict) -> bool:
    """Transcript evidence that a finished card's final notice was delivered to the chat and answered."""
    try:
        at = card.get("completed_at")
        if at is None:
            return False
        return core.ack_proven(state_dbs(), _lineage(sid), tid, float(at))
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
        out = core.record_turn_end(ledger(), session_id, open_card_ids(session_id), read_cards,
                                   observed_sig=observed)
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
    Cached for 3 s (the session list polls)."""
    if _off():
        return {}
    hit = _state.get("waiting_cache")
    mono = time.monotonic()
    if hit and mono - hit[0] < 3.0:
        return hit[1]
    try:
        out = core.pending_closeouts(ledger(), read_cards, proof=session_proof)
    except Exception:
        logger.debug("chat-closeout waiting source failed", exc_info=True)
        out = {}
    _state["waiting_cache"] = (mono, out)
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
        from hermes_state import SessionDB
        tip = SessionDB().get_compression_tip(sid)
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
              read: Callable[[list[str]], dict] = read_cards,
              proof: Optional[Callable[[str, str, dict], bool]] = None) -> list[dict]:
    """One close-out pass over the ledger. Returns what it did (for logs/tests)."""
    now = time.time() if now is None else now
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
        due = core.closeout_due(row, cards, unseen=sum(unseen.get(i, 0) for i in ids),
                                last_user_at=last_user(ids), now=now)
        if not due or not led.claim(sid, due, now):
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
            led.write(sid, wake_result="started")
            logger.info("chat-closeout: woke %s for its close-out (%d cards settled, %d new; wake #%d)", sid,
                        len(row["batch"]), len(new_ids), int(row.get("wake_count") or 0) + 1)
        else:
            # busy/refused/error: undo the coverage + count; a later tick (or the owning backend) retries
            led.write(sid, covered=set(row.get("covered") or []), closing=None, wakes=row.get("wakes") or [],
                      wake_count=int(row.get("wake_count") or 0))
            led.release(sid, due, result)
            logger.info("chat-closeout: close-out wake of %s deferred (%s)", sid, result)
        done.append({"session_id": sid, "result": result, "sig": due})
    return done


def post_inbox(title: str, body: str, key: str) -> None:
    store_py = _kanban_root() / "plugins" / "inbox" / "store.py"
    spec = importlib.util.spec_from_file_location("chat_closeout_inbox_store", store_py)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.Inbox().post(title, body, key=key, poster="chat-closeout")


def watchdog_tick(*, now: Optional[float] = None, mono: Optional[float] = None,
                  unseen: Optional[dict[str, int]] = None, post: Callable[[str, str, str], None] = post_inbox,
                  force: bool = False, read: Callable[[list[str]], dict] = read_cards) -> list[str]:
    """Flag stuck pipelines (once per chat+state): unseen kanban results > 30 min, or a settled batch with no
    close-out > 30 min. Conditions are observed every tick; flagging runs in one process per 15 min.

    Ages: the persisted wall-clock stamp must be > 30 min AND this process must have seen the condition for
    > 30 min of monotonic time, so a lid sleep (monotonic clock stops) or a restart never fakes a stale pipeline.
    The flag is recorded only after the Inbox post succeeded, so a failed post retries next run.
    Returns the flag keys raised."""
    now = time.time() if now is None else now
    mono = time.monotonic() if mono is None else mono
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
    live = {k for k, _, _ in candidates}
    for k in [k for k in seen_mono if k not in live]:
        if not _still_observed(k, unseen, led):
            seen_mono.pop(k, None)
    candidates = [c for c in candidates if not led.has_flag(c[0])]
    if not candidates or (not force and not led.lease("watchdog", core.WATCHDOG_EVERY_S, now)):
        return []
    raised: list[str] = []
    for key, title, msg in candidates:
        logger.warning("chat-closeout watchdog: %s", msg)
        try:
            post(title, msg, key)
        except Exception:
            logger.warning("chat-closeout: Inbox post failed for %s (retrying next run)", key, exc_info=True)
            continue
        led.flag_once(key, now)
        raised.append(key)
    return raised


def _still_observed(key: str, unseen: dict[str, int], led) -> bool:
    """Whether a remembered watchdog condition still holds (else its monotonic first-seen is forgotten)."""
    parts = key.split(":")
    if len(parts) >= 3 and parts[1] == "unseen":
        return unseen.get(parts[2], 0) > 0
    if len(parts) >= 4 and parts[1] == "no-closeout":
        row = led.get(parts[2]) or {}
        return row.get("settled_sig") == parts[3] and row.get("done_sig") != parts[3]
    return False


def _loop(stop: threading.Event) -> None:
    stop.wait(float(os.environ.get("CHAT_CLOSEOUT_FIRST_S", "20")))
    while not stop.is_set():
        if _server() is not None and not _off():
            for fn in (wake_tick, watchdog_tick):
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
    logger.info("chat-closeout: loaded owner-final-proof-v2 module=%s core=%s", __file__, core.__file__)
    ctx.register_hook("post_llm_call", post_llm_call)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("pre_final_gate", pre_final_gate)
    if not os.environ.get("HERMES_KANBAN_TASK"):
        _register_waiting_source()
        start_thread()

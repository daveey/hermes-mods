"""coach: one nudge bus. Agent channel = hooks here (hints the model reads, invisible to the user).
User channel = dashboard/plugin_api.py + desktop-plugins/coach (one-button panels above the composer)."""
from __future__ import annotations

import contextvars
import datetime
import hashlib
import importlib.util
import json
import logging
import threading
import os
import time
import sys
import types
import uuid
from pathlib import Path

log = logging.getLogger("coach")

# Nudge record events (see build(): one delivery and one outcome record per reply_nudge, by nudge_id).
_DELIVERY_EVENTS = ("reply_nudge_delivered", "reply_nudge_undelivered", "reply_nudge_abandoned")
_OUTCOME_EVENTS = ("reply_nudge_outcome", "nudge_outcome_lost", "nudge_outcome_subagent", "nudge_outcome_untracked",
                   "nudge_outcome_missing")
# signals.collect_coach may close a nudge from HANDOFF_GRACE_S (15 min) on; an in-process close of a nudge older than
# this first checks the log for that record, so a nudge never gets two.
_NUDGE_LATE_S = 14 * 60
_HERE = Path(__file__).resolve().parent
# Process-wide handover of the coach's live per-session state across plugin reloads (see _live_state).
_LIVE_KEY = "_coach_live_state"


def _live_state(root) -> dict:
    """The live state of the coach registered for this home, shared by every build() of this process.

    hermes-sync's mods-reload force-reloads the plugin in running gateways/serves whenever a coach file changes: the
    manager purges this package and calls register() again, often in the middle of a turn. A fresh build() used to
    start from empty sessions (turn counter, ask, tool log, pending nudges, outcome slot), so a nudge the old build
    had just returned got no delivery and no outcome record, its revision was never judged and the signals sweep
    filed it as ignored. The state lives under a sys.modules key outside the plugin namespace (the purge leaves it),
    one entry per resolved coach store root, so two profiles' coaches in one process never share sessions."""
    reg = sys.modules.get(_LIVE_KEY)
    if reg is None or not isinstance(getattr(reg, "homes", None), dict):
        reg = types.ModuleType(_LIVE_KEY)
        reg.__dict__["homes"] = {}
        sys.modules[_LIVE_KEY] = reg
    try:
        key = str(Path(root).resolve())
    except OSError:
        key = str(root)
    return reg.__dict__["homes"].setdefault(key, {})  # dict.setdefault: atomic under the GIL


def _last_assistant(history) -> str:
    """The latest assistant message with text in the hook's conversation_history (fallback when the coach's own
    per-session state has no last reply yet, e.g. the first turn after a backend restart)."""
    for m in reversed(history or []):
        if isinstance(m, dict) and m.get("role") == "assistant":
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(str(p.get("text") or "") for p in c if isinstance(p, dict))
            if isinstance(c, str) and c.strip():
                return c
    return ""


def _request_scope(history, user_message) -> str:
    """Earlier user restraints for the complaint judge (self_heal.request_scope); fail-open."""
    try:
        return _load("self_heal").request_scope(history, user_message)
    except Exception as exc:
        log.debug("coach request scope unavailable: %s", exc)
        return ""


# Bootstrap the stateless helper without relying on a package name or sys.path.
_cache_spec = importlib.util.spec_from_file_location("_coach_module_cache", _HERE / "module_cache.py")
assert _cache_spec and _cache_spec.loader
_cache = importlib.util.module_from_spec(_cache_spec)
_cache_spec.loader.exec_module(_cache)


def _bg_thread(fn, *args) -> None:
    """Run `fn` on a daemon thread inside a copy of the caller's context. A bare Thread starts with an empty
    context: on a multiplexed backend the profile secret scope (agent/secret_scope.py, a ContextVar) is then unset and
    every aux judge call raises UnscopedSecretError. The hub's complaint judge failed that way, silently, for a
    week (plugin-89461de0ed)."""
    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(fn, *args), daemon=True).start()


def _load(name: str):
    """Load a checkout-local sibling, including its mtime hot reload."""
    return _cache.load_sibling(_HERE, name)


def _adopt(live: dict, coach, rules) -> int:
    """Point a new build's AgentCoach at the live sessions of the previous build of this process (plugin reload).
    Fills per-session keys this AgentCoach version creates and the old one did not (agent_rules may have changed in
    the same reload). Returns the number of nudges carried over still waiting for a record."""
    sessions = live.setdefault("sessions", coach.sessions)
    if sessions is coach.sessions:
        return 0
    blank = rules.AgentCoach()
    for sid, s in list(sessions.items()):
        if isinstance(s, dict):
            for k, v in blank._s(sid).items():
                s.setdefault(k, v)
            if not isinstance(s.get("loop"), dict):
                s["loop"] = {"fails": []}
    coach.sessions = sessions
    open_ids: set = set()
    for s in list(sessions.values()):  # old in-flight hooks may add sessions during this telemetry scan
        loop = s.get("loop") if isinstance(s, dict) else {}
        p = loop.get("pending_nudge") if isinstance(loop, dict) else None
        if isinstance(p, dict):
            open_ids.update([p.get("nudge_id") or "legacy"] if "turn" in p else p)
        o = loop.get("outcome_nudge") if isinstance(loop, dict) else None
        if isinstance(o, dict):
            open_ids.add(o.get("nudge_id") or "outcome")
    return len(open_ids)


def build(store=None, checkout=None, selfheal_sync=False, selfheal_llm=None, selfheal_confirm="auto", live=None):
    """The three hook callbacks around one AgentCoach (exposed for tests). `selfheal_sync` runs the self-heal
    detectors inline (tests); in production they run in a daemon thread so a judge call never delays a turn.
    `live` (register() passes _live_state(store.root)) carries the per-session coach state across rebuilds of the
    same process, so a plugin reload mid-turn keeps the turn's open nudges; None = private state (tests)."""
    core, rules = _load("core"), _load("agent_rules")
    store = store or core.Store()
    coach = rules.AgentCoach(checkout if checkout is not None else rules.default_live_checkout(), signals=store)
    goal_gate = _load("goal_disposition").build_gate(store)
    shared: dict = live if live is not None else {}
    coach.hint_lock = shared.setdefault("hint_lock", coach.hint_lock)
    try:
        adopted_sessions = len(live.get("sessions") or {}) if live is not None else 0
        carried = _adopt(shared, coach, rules)
        if adopted_sessions:
            store.log({"event": "coach_state_adopted", "sessions": adopted_sessions, "open_nudges": carried})
    except Exception as exc:  # telemetry/schema faults must not erase the old build's live ownership
        log.debug("coach live state handover: %s", exc)
        coach.sessions = shared.setdefault("sessions", coach.sessions)

    authority_users: dict = shared.setdefault("authority_users", {})
    authority_rows: dict = shared.setdefault("authority_rows", {})
    cap_due_seen: set = shared.setdefault("root_cap_due_seen", set())
    # session -> dollar totals the user approved by answering a priced ask with a bare yes ("approved").
    authority_asked: dict = shared.setdefault("authority_asked", {})

    def pre_llm_call(session_id="", user_message=None, platform="", parent_session_id="", conversation_history=None,
                     **_):
        # Capture before other optional coaching work. Never use tool/assistant claims,
        # worker briefs, or an unverified remote-origin link as user authority.
        try:
            rows = _load("goal_scope").recover_scope(
                session_id, user_message, conversation_history,
                canonical_user_content=_.get("canonical_user_content"))
            authority_users[session_id] = [r.get("content") for r in rows
                                          if isinstance(r.get("content"), str)]
            # typed rows keep the native message identity for root_cap provenance (strings alone lose it)
            authority_rows[session_id] = [{"content": r.get("content"), "message_uid": r.get("message_uid")}
                                          for r in rows if isinstance(r.get("content"), str)]
        except Exception as exc:
            authority_users[session_id] = []
            authority_rows[session_id] = []
            log.debug("coach compute authority scope unavailable: %s", exc)
        try:
            # The figure comes from the assistant's ask; the authority from the user's own typed yes to it.
            authority_asked[session_id] = list(_load("compute_envelope").answered_asks(
                conversation_history, user_message, is_user_evidence=_load("goal_scope").is_evidence_row))
        except Exception as exc:
            authority_asked[session_id] = []
            log.debug("coach compute authority answered asks unavailable: %s", exc)
        cap_notes = ""
        try:  # existing owner recovery path: due/conflicting temporary cap exceptions ride this turn's coach context
            rc = _load("root_cap")
            home_dirs = [os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")]
            rc.report_due(store.root, store.log, cap_due_seen, homes=home_dirs)  # log once per process
            cap_notes = " ".join(rc.due_notices(store.root, session_id, log=None, homes=home_dirs))
        except Exception as exc:
            log.debug("coach root_cap due report: %s", exc)
        try:
            if not session_id:
                return {"context": cap_notes} if cap_notes else None
            goal_gate.capture(session_id=session_id, user_message=user_message,
                              conversation_history=conversation_history,
                              canonical_user_content=_.get("canonical_user_content"))
            # Seconds since this session's last recorded activity, read before user_turn bumps it: the length of
            # a backend stop when this turn is core's resume note (self_heal.resume_signal).
            last_seen = store.session(session_id).get("updated")
            turn_at = store.clock()
            rec = store.user_turn(session_id, user_message) or {}
            hint = coach.turn_start(session_id, user_message, platform=platform or "",
                                    parent_session_id=parent_session_id or "",
                                    replied_before=bool(_last_assistant(conversation_history)))
            _wait_turn(session_id, user_message, platform, parent_session_id)
            # The fix-loop window survives process boundaries (`hermes chat -q --resume`, backend restarts): the
            # in-memory loop state is re-seeded from the session file, and windows count persistent user turns.
            st = coach.loop_state(session_id)
            if not st.get("last_reply") and isinstance(rec.get("loop"), dict):
                st.update(rec["loop"])
            turn = rec.get("turns") or coach.turn_number(session_id)
            # a nudge of an earlier turn whose continuation was never checked (a notice or the user's next message
            # superseded it, the turn died): no check can come for it now
            _close_open(session_id, "next_turn", "turn_changed", before_turn=coach.turn_number(session_id))
            # Lessons judge nearly every message, so it runs in the background; its hint rides the next tool result
            # (or the next turn). The fix-loop judge is prefilter-gated and its hint matters before the first patch,
            # so it stays inline.
            prev = coach.loop_state(session_id).get("last_reply") or ""
            # Check before scheduling: a disabled call must not wake after its caller's
            # temporary environment has been restored and start an auxiliary LLM call.
            if os.environ.get("COACH_LESSONS", "1").strip().lower() not in ("0", "off", "false", "no"):
                _bg_thread(_lesson_bg, session_id, user_message, prev)
            _selfheal_run(_selfheal_complaint, session_id, user_message,
                          prev or _last_assistant(conversation_history), platform, parent_session_id,
                          list(coach.loop_state(session_id).get("last_log") or []),
                          (turn_at - last_seen) if isinstance(last_seen, (int, float)) else None, turn_at,
                          _request_scope(conversation_history, user_message), str(_.get("input_origin") or ""))
            loop_hint = _fix_loop(session_id, user_message, turn)
            relay_hint = _relay_repeat(session_id, user_message, platform, parent_session_id)
            plain_hint = _status_plain(session_id, user_message, platform, parent_session_id)
            stall_hint = _stalled_goal(session_id, platform)
            hint = " ".join(h for h in (f"[coach] {hint}" if hint else "", loop_hint or "", relay_hint or "",
                                        plain_hint or "", stall_hint or "", cap_notes) if h)
            return {"context": hint} if hint else None
        except Exception as exc:
            log.debug("coach pre_llm_call: %s", exc)
            return {"context": cap_notes} if cap_notes else None

    def post_tool_call(tool_name="", args=None, result=None, session_id="", status=None, duration_ms=0,
                       error_type=None, error_message=None, **_):
        try:
            goal_gate.observe(session_id=session_id, tool_name=tool_name, args=args, result=result,
                              status=status, tool_call_id=_.get("tool_call_id"))
            _load("root_cap").after(tool_name, args, session_id, store.root, tool_call_id=_.get("tool_call_id"))
            coach.hint_tool_end(session_id, _.get("tool_call_id"), tool_name,
                                args if isinstance(args, dict) else {}, result, status)
            coach.observe(session_id, tool_name, args, result, status, duration_ms or 0, error_type, error_message)
        except Exception as exc:
            log.debug("coach post_tool_call: %s", exc)
        try:  # a lifecycle-guard fail-closed refusal of a command with no lifecycle text: self-heal, every occurrence
            if result is not None and "the lifecycle guard could not scan this" in str(result):
                _selfheal_run(_selfheal_guard, session_id, tool_name, args, result)
        except Exception as exc:
            log.debug("coach post_tool_call guard refusal: %s", exc)

    def transform_tool_result(tool_name="", result=None, session_id="", args=None, **_):
        try:
            coach.check_live(session_id, tool_name, args)
            coach.check_kanban_db_write(session_id, tool_name, args)
            coach.check_hub_ssh_heavy(session_id, tool_name, args)
            coach.check_worker_ssh_refused(session_id, tool_name, args, result)
            coach.check_widget_send(session_id, tool_name, args)
            coach.check_plan_first(session_id, tool_name, args)
            coach.check_empty_wait(session_id, tool_name, args, result)
            coach.check_cleanup_sweeper(session_id, tool_name, args, result)
            hint = coach.take(session_id) if isinstance(result, str) else None
            return rules.attach_hint(result, hint) if hint else None
        except Exception as exc:
            log.debug("coach transform_tool_result: %s", exc)
            return None

    # (session_id, where) already given the compute-envelope note
    envelope_told: set = shared.setdefault("envelope_told", set())

    def _persisted_asks(session_id) -> list:
        """Figures the user approved with a bare yes to a priced ask that compaction moved out of the live window
        (compute_envelope.persisted_asks over the persisted transcript). Read only for a would-be compute-authority
        hold; logged, so a cleared hold is visible in the coach log. A failed read credits nothing.

        A Kanban worker's approval lives in its card's origin chat (often on another gateway), never in the worker's
        own transcript or the card body: origin_authority reads that chat under the same rules (a typed yes to a
        priced ask, or nothing)."""
        if not session_id:
            return []
        got: list = []
        try:
            got = list(_load("compute_envelope").persisted_asks(session_id, _load("goal_scope")))
            store.log({"event": "authority_persisted_read", "session_id": session_id, "ok": True, "usd": got[:20]})
        except Exception as exc:
            store.log({"event": "authority_persisted_read", "session_id": session_id, "ok": False,
                       "error": f"{type(exc).__name__}: {exc}"[:200]})
        try:
            got += list(_load("origin_authority").origin_asks(
                _load("goal_scope"), _load("compute_envelope"),
                log=lambda rec: store.log({**rec, "session_id": session_id}),
                durable=store.root / "origin_authority.json"))
        except Exception as exc:
            store.log({"event": "authority_origin_read", "session_id": session_id, "ok": False,
                       "error": f"{type(exc).__name__}: {exc}"[:200]})
        return got

    # ── nudge records: every reply_nudge gets a nudge_id, and exactly one delivery record (reply_nudge_delivered |
    # _undelivered | _abandoned) and one outcome record (reply_nudge_outcome | nudge_outcome_lost |
    # nudge_outcome_untracked; signals.collect_coach adds nudge_outcome_subagent / _missing and closes nudges whose
    # backend never checked the session again). A session may hold several open nudges (loop["pending_nudge"]:
    # nudge_id -> entry), each closed by the first of: the continuation's check (attempt >= 1, or an attempt-0 check
    # of a different reply in the same turn: core restarts its verify attempts after a mid-turn compaction), the end
    # of the turn (post_llm_call), or the next turn (pre_llm_call).

    def _new_nudge_id() -> str:
        return "n_" + uuid.uuid4().hex[:16]

    def _sig(text) -> str:
        return hashlib.sha1(" ".join(str(text or "").split()).encode("utf-8", "replace")).hexdigest()[:12]

    def _ids(n: dict) -> dict:
        return {k: n[k] for k in ("nudge_id", "draft_id") if n.get(k)}

    def _closed_elsewhere(n: dict, events: tuple) -> bool:
        """The signals sweep already wrote this record for the nudge (it closes nudges open past NUDGE_OPEN_S): a
        late in-process close must not add a second one. Only scanned for nudges that old, so normally free."""
        nid, at = n.get("nudge_id"), float(n.get("at") or 0)
        if not nid or not at or time.time() - at < _NUDGE_LATE_S:
            return False
        try:
            with (store.root / "log.jsonl").open(encoding="utf-8", errors="replace") as f:
                return any(nid in ln and any(f'"{e}"' in ln for e in events) for ln in f)
        except OSError:
            return False

    def _log_delivery(session_id, n, event, **extra):
        late = _closed_elsewhere(n, _DELIVERY_EVENTS)
        store.log({"event": "reply_nudge_record_late" if late else event, **({"would_be": event} if late else {}),
                   "session_id": session_id, "rule": n.get("rule", ""),
                   "secs": round(time.time() - float(n.get("at") or time.time()), 1), **_ids(n), **extra})

    def _log_outcome(session_id, n, event, **extra):
        late = _closed_elsewhere(n, _OUTCOME_EVENTS)
        store.log({"event": "reply_nudge_record_late" if late else event, **({"would_be": event} if late else {}),
                   "session_id": session_id, "rule": n.get("rule") or "handoff", **_ids(n), **extra})

    def _pending(session_id, create=False) -> dict:
        st = coach.loop_state(session_id)
        p = st.get("pending_nudge")
        if isinstance(p, dict) and "turn" in p:  # one slot, from an older plugin's persisted loop state
            p = st["pending_nudge"] = {p.get("nudge_id") or "legacy": p}
        if not isinstance(p, dict):
            if not create:
                return {}
            p = st["pending_nudge"] = {}
        return p

    def _park_nudge(session_id, rec, final_response):
        """Wait for this nudge's continuation (closed by _nudge_delivery / _close_open)."""
        nid = rec.get("nudge_id") or _new_nudge_id()
        _pending(session_id, create=True)[nid] = {
            "nudge_id": nid, "turn": coach.turn_number(session_id), "rule": rec.get("rule", ""), "at": time.time(),
            "sig": _sig(final_response), **({"draft_id": rec["draft_id"]} if rec.get("draft_id") else {})}

    def _close_open(session_id, delivery_reason, outcome_reason, before_turn=None):
        """No continuation check can come for these nudges any more: the turn ended (before_turn None) or a later
        turn began (before_turn = its number; only older nudges close). Fail-open."""
        try:
            st = coach.loop_state(session_id)
            p = _pending(session_id)
            for nid, n in list(p.items()):
                if before_turn is None or int(n.get("turn") or 0) < before_turn:
                    p.pop(nid, None)
                    _log_delivery(session_id, n, "reply_nudge_undelivered", reason=delivery_reason)
            o = st.get("outcome_nudge")
            if o and (before_turn is None or int(o.get("turn") or 0) < before_turn):
                st.pop("outcome_nudge", None)
                _log_outcome(session_id, o, "nudge_outcome_lost", reason=outcome_reason, nudge_turn=o.get("turn"),
                             turn=coach.turn_number(session_id))
        except Exception as exc:
            log.debug("coach close open nudges: %s", exc)

    def _envelope(session_id, where, text, may_hold=True, sink=None, ask_text=None):
        """Compute-envelope check (compute_envelope.py). First hit per session/surface -> the note (the caller holds
        the block / the reply); a repeat after the note goes through and is logged as an agent fault. A reply hit is a
        reply_nudge with its own nudge_id (returned in `sink["rec"]` so the caller parks it for delivery); no judge
        re-checks the revision, so its outcome record (`nudge_outcome_untracked`) is written with it.
        `ask_text` (default `text`) is the part addressed to a human, the only part the under-use check reads."""
        try:
            ce = _load("compute_envelope")
            if not ce.enabled():
                return None
            hit = ((ce.authority(text, authority_users.get(session_id, ()), authority_asked.get(session_id, ()),
                                 persisted=lambda: _persisted_asks(session_id)) if text else None)
                   or ce.classify(text if ask_text is None else ask_text))
            if not hit:
                return None
            rule = hit.get("rule", "compute_envelope")
            key = (session_id or "", where, rule)
            if key in envelope_told or not may_hold:
                if key in envelope_told:
                    store.log({"event": "agent_fault", "rule": rule, "session_id": session_id,
                               "where": where, "evidence": hit["sentence"][:200]})
                return None
            envelope_told.add(key)
            rec = {"event": "agent_hint" if where == "block" else "reply_nudge", "rule": rule,
                   "session_id": session_id, "where": where, "p": 1.0, "usd": hit.get("usd"),
                   "evidence": hit["sentence"][:200]}
            if where != "block":
                rec["nudge_id"] = _new_nudge_id()
            store.log(rec)
            if where != "block":
                _log_outcome(session_id, rec, "nudge_outcome_untracked", reason="rule_not_judged")
                if sink is not None:
                    sink["rec"] = rec
            return ce.authority_note(hit) if rule == "compute_authority" else ce.note(hit, where)
        except Exception as exc:
            log.debug("coach compute_envelope: %s", exc)
            return None

    def envelope_gate(session_id="", platform="", final_response="", attempt=0, turn_origin=None, **_):
        """pre_final_gate: hold an in-envelope compute/spend ask once, ahead of ordinary gates (user-steps would
        otherwise push it into a formal approval request and replace it before pre_verify sees it). A fork turn
        (`turn_origin` set: background review, side question) is not a reply to the user: never judged."""
        if turn_origin or attempt or not session_id or platform in ("cron",) or not isinstance(final_response, str):
            return None
        sink: dict = {}
        msg = _envelope(session_id, "reply", final_response, sink=sink)
        if msg and sink.get("rec"):
            _park_nudge(session_id, sink["rec"], final_response)  # the continuation's next check delivers it
        return {"hold": True, "nudge": msg} if msg else None

    envelope_gate.assess_before_holds = True

    # session -> this user turn's long_wait_pending_reply record (long_wait.py; self-heal agent-f1f8b24e9d)
    wait_turns: dict = shared.setdefault("long_wait_turns", {})

    def _wait_turn(session_id, user_message, platform="", parent_session_id=""):
        try:
            lw = _load("long_wait")
            wait_turns[session_id] = {"turn": coach.turn_number(session_id), "replied": False, "held": False,
                                      "live": lw.live_turn(platform or "", parent_session_id or ""),
                                      "delay": lw.asks_delay(rules.request_text(user_message))}
        except Exception as exc:
            wait_turns.pop(session_id, None)  # unknown turn: the rule stays quiet
            log.debug("coach long_wait turn: %s", exc)

    def on_interim_message(session_id="", text="", **_):
        coach.hint_report(session_id, text)
        if isinstance(text, str) and text.strip() and session_id in wait_turns:
            wait_turns[session_id]["replied"] = True
            try:  # live_chat_sleep: how fresh the user's last status is
                wait_turns[session_id]["replied_at"] = _load("long_wait").now()
            except Exception as exc:
                log.debug("coach long_wait interim: %s", exc)

    def pre_tool_call(tool_name="", args=None, session_id="", **_):
        coach.hint_tool_start(session_id, _.get("tool_call_id"))
        profiler = _load("profiler_safety")
        if profiler.blocked(tool_name, args):
            store.log({"event": "agent_block", "rule": "profiler_safety", "tool": tool_name})
            return {"action": "block", "message": profiler.MESSAGE}
        try:  # a count/LIKE steer-ACK poll matches the steer itself (steer_receipt_guard.py)
            steer = _load("steer_receipt_guard")
            if steer.blocked(tool_name, args):
                store.log({"event": "agent_block", "rule": "steer_receipt", "tool": tool_name,
                           "session": session_id})
                return {"action": "block", "message": steer.MESSAGE}
        except Exception as exc:
            log.debug("coach steer_receipt_guard: %s", exc)
        msg = _future_clock(session_id, tool_name, args)
        if msg:
            return {"action": "block", "message": msg}
        try:  # one long foreground wait while the live user has had no reply this turn (long_wait.py)
            lw = _load("long_wait")
            msg = lw.check(wait_turns, session_id, tool_name, args, store.log)
            # ...or no fresh status for a while (live_chat_sleep; a loop with sleep is unbounded)
            msg = msg or lw.check_stale(wait_turns, session_id, tool_name, args, store.log)
        except Exception as exc:
            log.debug("coach long_wait: %s", exc)
            msg = None
        if msg:
            return {"action": "block", "message": msg}
        try:  # frozen-rollout cap: a queue notice is not permission (root_cap.py)
            rc = _load("root_cap")
            cap = rc.check(tool_name, args, session_id, store.root, authority_rows.get(session_id, ()),
                           tool_call_id=_.get("tool_call_id"))
        except Exception as exc:
            # root_cap.check never raises; this is a load/import fault. Refuse ONLY a call that names the guarded
            # key (targeted uncertainty); every other tool keeps working under the same fault.
            log.debug("coach root_cap: %s", exc)
            cap = None
            try:
                blob = json.dumps(args, default=str)
            except Exception:
                blob = str(args)
            if tool_name in ("terminal", "execute_code") and "max_in_progress" in blob:
                cap = {"message": "Root kanban.max_in_progress is guarded and the guard could not load "
                                  f"({type(exc).__name__}); this targeted change is refused until it can be verified. "
                                  "Unrelated config and tools are unaffected.",
                       "event": {"event": "agent_block", "rule": "root_cap", "key": "kanban.max_in_progress",
                                 "session": session_id, "fault": type(exc).__name__}}
        if cap:
            store.log(cap["event"])
            return {"action": "block", "message": cap["message"]}
        if tool_name in ("kanban_block", "kanban_create", "tool_call"):
            try:
                ce = _load("compute_envelope")
                text = ce.block_text(tool_name, args)
                ask = ce.block_text(tool_name, args, human_only=True)
            except Exception as exc:
                log.debug("coach compute_envelope: %s", exc)
                text = ask = ""
            msg = _envelope(session_id, "block", text, ask_text=ask) if text or ask else None
            if msg:
                return {"action": "block", "message": msg}
        if tool_name in ("kanban_block", "kanban_create", "tool_call"):
            msg = _block_handoff(session_id, tool_name, args)
            if msg:
                return {"action": "block", "message": msg}
        try:
            what = rules.blocks_live_checkout(tool_name, args if isinstance(args, dict) else {}, coach.checkout)
        except Exception as exc:
            log.debug("coach pre_tool_call: %s", exc)
            return None
        if not what:
            return None
        log.info("coach blocked live-checkout change: %s", what)
        try:
            store.log({"event": "agent_block", "rule": "live_checkout", "what": what})
        except Exception:
            pass
        return {"action": "block", "message": rules.block_message(what, coach.checkout)}

    # (session_id, evidence) whose future-dated card stamp was refused once (agent-748d257889)
    clock_held: set = shared.setdefault("clock_held", set())

    def _future_clock(session_id, tool_name, args):
        """A Kanban card text (comment, summary, block reason, new card) stamping a past-tense Pacific time later than
        the real clock: refuse it ONCE per session and stamp with the real local time; the same call again goes
        through (logged), so it can never loop. Fail-open."""
        try:
            if tool_name != "tool_call" and not str(tool_name).startswith("kanban_"):
                return None
            rc = _load("reply_check")
            if "future_clock" not in rc.settings()["rules"]:
                return None
            text = rc.durable_text(tool_name, args)
            ev = rc.future_clock(text) if text else None
            if not ev:
                return None
            key = (session_id or "", ev)
            if key in clock_held:
                store.log({"event": "future_clock_repeat", "rule": "future_clock", "session_id": session_id,
                           "tool": tool_name, "evidence": ev[:200]})
                return None
            clock_held.add(key)
            store.log({"event": "agent_block", "rule": "future_clock", "session_id": session_id, "tool": tool_name,
                       "evidence": ev[:200]})
            return rc.future_clock_nudge(ev)
        except Exception as exc:
            log.debug("coach future_clock: %s", exc)
            return None

    # (session_id, card) whose human-facing block was held once (agent-19492bff76)
    block_held: set = shared.setdefault("block_held", set())

    def _block_handoff(session_id, tool_name, args):
        """A Kanban ask to the user that the agent could plausibly do itself: hold it ONCE per session and card with
        the handoff nudge; the same block again goes through (logged), so it can never loop. Fail-open."""
        try:
            rc = _load("reply_check")
            cfg = rc.settings()
            if "handoff" not in cfg["rules"] or not rc.block_time_enabled():
                return None
            got = rc.human_block(tool_name, args, os.environ.get("HERMES_KANBAN_TASK", ""))
            if not got:
                return None
            text, card = got
            key = (session_id or "", card)
            if key in block_held:
                store.log({"event": "block_handoff_repeat", "rule": "block_handoff", "session_id": session_id,
                           "card": card})
                return None
            ask, log_lines = coach.turn_context(session_id) if session_id else ("", [])
            hit = rc.block_handoff(text, ask, log_lines, rc.aux_judge, threshold=cfg["threshold"],
                                   thresholds=cfg.get("thresholds"))
            if not hit:
                store.log({"event": "block_checked", "rule": "block_handoff", "session_id": session_id, "card": card})
                return None
            block_held.add(key)
            store.log({"event": "agent_block", "rule": "block_handoff", "session_id": session_id, "card": card,
                       "tool": tool_name, "p": hit["p"], "evidence": hit["evidence"]})
            return hit["nudge"]
        except Exception as exc:
            log.debug("coach block handoff: %s", exc)
            return None

    def _fix_loop(session_id, user_message, turn=None):
        """Judge whether this user message reports that the agent's last fix failed (once per turn, sync,
        prefilter-gated, short timeout). Fail-open."""
        try:
            fl = _load("fix_loop")
            cfg = fl.settings()
            if not cfg["on"] or not isinstance(user_message, str):
                return None
            st = coach.loop_state(session_id)
            s1 = _load("system_one")
            m = s1.mode()
            turn = turn or coach.turn_number(session_id)
            hit = fl.assess(st, turn, user_message, fl.aux_judge, threshold=cfg["threshold"],
                            screen=s1.fixloop_screen if m != "off" else None, screen_below=s1.below(),
                            shadow=m == "shadow", log=s1.log)
            if not hit:
                return None
            store.log({"event": "agent_hint", "session_id": session_id, "rule": "fix_loop", "n": hit["n"],
                       "p": hit["p"], "issue": hit["issue"]})
            store.confirmed_fail(session_id)
            return " ".join(h for h in (hit["hint"], _fixloop_card(session_id, st, hit, user_message, turn)) if h)
        except Exception as exc:
            log.debug("coach fix_loop: %s", exc)
            return None

    def _relay_repeat(session_id, user_message, platform="", parent_session_id=""):
        """The user asked again while the last reply relayed an unfinished owner (relay_repeat.py, agent-1406d53913):
        one "take over" hint per turn, and the turn is marked so its reply gets the report_only second opinion.
        Live chats only: a cron run, a subagent or a Kanban worker has no one asking again. Fail-open."""
        try:
            if platform in ("cron", "kanban", "subagent") or parent_session_id:
                return None
            rr = _load("relay_repeat")
            if not rr.enabled():
                return None
            turn = coach.turn_number(session_id)
            hit = rr.assess(coach.loop_state(session_id), turn, user_message)
            if not hit:
                return None
            store.log({"event": "agent_hint", "session_id": session_id, "rule": "relay_repeat", "n": hit["n"],
                       "kind": hit.get("kind", "relay"), "turn": turn, "cards": hit["cards"][:5],
                       "evidence": hit["phrase"][:200]})
            return hit["hint"]
        except Exception as exc:
            log.debug("coach relay_repeat: %s", exc)
            return None

    def _status_plain(session_id, user_message, platform="", parent_session_id=""):
        """A person's progress ask in a live chat -> one outcome-first hint per turn (reply_check.status_plain_hint,
        agent-48e909978c). Advisory only; never holds a reply. Fail-open."""
        try:
            hint = _load("reply_check").status_plain_hint(user_message, platform or "", parent_session_id or "")
            if not hint:
                return None
            st, turn = coach.loop_state(session_id), coach.turn_number(session_id)
            if st.get("status_plain_turn") == turn:
                return None
            st["status_plain_turn"] = turn
            store.log({"event": "agent_hint", "session_id": session_id, "rule": "status_plain", "turn": turn,
                       "evidence": " ".join(str(user_message).split())[:200]})
            return hint
        except Exception as exc:
            log.debug("coach status_plain: %s", exc)
            return None

    def _stalled_goal(session_id, platform=""):
        """This chat's card graph has a delivery card that hasn't moved for a day while the rest of the graph keeps
        spending runs -> one "take over" line per incident (stalled_goal.py). Fail-open."""
        try:
            if platform in ("cron",):
                return None
            sg = _load("stalled_goal")
            if not sg.enabled():
                return None
            ids = _load("reply_check")._lineage(session_id)
            return sg.session_hint(ids, store.root, log=store.log)
        except Exception as exc:
            log.debug("coach stalled_goal: %s", exc)
            return None

    def _fixloop_card(session_id, st, hit, user_message, turn):
        """2nd confirmed failure in the window -> one read-only review card pinned to the strong model."""
        try:
            so = _load("second_opinion")
            created = []

            def create(args):
                res = so.create_card(args)
                created.append(res)
                return res
            extra = so.fixloop_review(st, session_id, turn, hit, user_message, create=create)
            if extra and created:
                store.log({"event": "fixloop_card", "session_id": session_id, "task_id": created[0].get("task_id"),
                           "n": hit["n"], "issue": hit["issue"]})
            elif created:  # the tool refused (e.g. a fenced subprocess of a Kanban worker): audit it, hint unchanged
                store.log({"event": "fixloop_card_error", "session_id": session_id,
                           "error": str(created[0].get("error") or created[0])[:300]})
            return extra
        except Exception as exc:
            log.debug("coach fix_loop: %s", exc)
            return None

    def _diff_review(session_id, so, hit, ask, log_lines, changed_paths, final_response, repos):
        """One bounded strong-model review of this turn's diff; its findings become the pre_verify nudge."""
        coach.loop_state(session_id)["diff_reviewed"] = coach.turn_number(session_id)
        t0 = time.time()
        since = getattr(coach, "turn_started", lambda _s: None)(session_id)
        try:
            rc = _load("reply_check")
            v = so.diff_review(ask, final_response, log_lines, changed_paths, since, repos=repos,
                               llm=lambda s, p: so.aux_review(
                                   s, p, timeout=rc.call_timeout(so.setting("diff_review_timeout_s"))))
        except Exception as exc:
            log.debug("coach diff_review: %s", exc)
            v = None
        store.log({"event": "diff_review", "session_id": session_id, "p": hit["p"], "ok": bool(v),
                   "repos": (v or {}).get("repos") or list(repos), "findings": len((v or {}).get("findings") or []),
                   "supported": (v or {}).get("supported"), "diff_chars": (v or {}).get("diff_chars"),
                   "secs": round(time.time() - t0, 1)})
        return so.review_nudge(v, hit.get("evidence") or "") if v else None

    def _lesson_bg(session_id, user_message, prev):
        h = _lesson(session_id, user_message, prev)
        if h:
            coach.queue_hint(session_id, h)

    def _lesson(session_id, user_message, prev=None):
        """A correction that implies a standing rule -> candidate lesson (promoted on recurrence) + agent hint."""
        try:
            ls = _load("lessons")
            if os.environ.get("COACH_LESSONS", "1").strip().lower() in ("0", "off", "false", "no"):
                return None
            if not isinstance(user_message, str):
                return None
            if prev is None:
                prev = coach.loop_state(session_id).get("last_reply") or ""
            lstore = ls.LessonStore(store.root)
            v = ls.assess(prev, user_message, ls.aux_judge, wide=True, known=lstore.known())
            if not v:
                return None
            c = lstore.add(v, session_id, example=user_message)
            if c["status"] == "saved":  # already in memory: just remind, don't ask to save again
                store.log({"event": "lesson", "session_id": session_id, "slug": v["slug"], "status": "saved"})
                return ls.hint(v, promoted=False)
            promoted = c["status"] == "promoted"
            store.log({"event": "lesson", "session_id": session_id, "slug": v["slug"], "status": c["status"],
                       "sessions": len(c["sessions"])})
            return ls.hint(v, promoted)
        except Exception as exc:
            log.debug("coach lessons: %s", exc)
            return None

    def _selfheal_run(fn, *args):
        """Self-heal detectors: fail-open, off the critical path unless selfheal_sync."""
        try:
            if _load("self_heal").mode() == "off":
                return
            if selfheal_sync:
                fn(*args)
            else:
                _bg_thread(fn, *args)
        except Exception as exc:
            log.debug("coach self_heal: %s", exc)
            try:  # a thread that could not start, or a self_heal module that failed to load, is a row
                store.log({"event": "selfheal_judged", "session_id": args[0] if args else "", "outcome": "error",
                           "stage": "run", "detector": getattr(fn, "__name__", ""),
                           "error": f"{type(exc).__name__}: {exc}"[:300]})
            except Exception:
                pass

    def _confirm(sh):
        """Second-stage judge: explicit `selfheal_confirm`, else (production, no injected judge) the configured
        `coach_defect_confirm` aux model, else none."""
        if selfheal_confirm != "auto":
            return selfheal_confirm
        return None if selfheal_llm else sh.confirm_judge()

    def _selfheal_complaint(session_id, user_message, prev, platform="", parent_session_id="", tool_log=None,
                            gap_s=None, turn_at=None, scope="", input_origin=""):
        # Every judged turn leaves a `selfheal_judged` row (outcome, score, stages, error), and a failure anywhere
        # in here is a row too: a week-long judge outage once looked exactly like "no complaints" (plugin-89461de0ed).
        sh, trace, started = None, {}, time.time()
        try:
            sh = _load("self_heal")
            # core's pre_llm_call `input_origin` ("agent" for an agent script's or agent-created chat, "human", or
            # "" on an older core): an agent's steering turn is no complaint (agent-d925f95adc).
            origin = {"input_origin": input_origin} if input_origin else {}
            if not sh.user_facing(platform, parent_session_id, **origin):
                why = sh.skip_reason(platform, parent_session_id, **origin) if hasattr(sh, "skip_reason") else None
                if why:
                    store.log({"event": "selfheal_judged", "session_id": session_id, "platform": platform,
                               "outcome": "skipped", "why": why})
                return
            why = sh.agent_session(session_id) if hasattr(sh, "agent_session") else None
            if why:  # older core (no origin): this host's state.db row is hidden or scratch
                store.log({"event": "selfheal_judged", "session_id": session_id, "platform": platform,
                           "outcome": "skipped", "why": why})
                return
            if turn_at is not None and sh.is_pause_note(user_message):
                # The coach file was last written when the paused turn STARTED (turn start/end only): time the pause
                # from the turn's last assistant/tool row instead (gateway-833d39a686). Unknown stays quiet; core's
                # 15-min stuck-pause WARNING is the backstop for a real stall.
                gap_s = sh.pause_gap_s(session_id, turn_at)
                if gap_s is None:
                    store.log({"event": "selfheal_skip", "mode": sh.mode(), "session_id": session_id,
                               "fingerprint": sh.fingerprint("gateway", "update pause did not continue in time"),
                               "why": "pause_gap_unknown"})
            hit = sh.detect_complaint(prev or "", user_message, llm=selfheal_llm, threshold=sh.threshold(),
                                      confirm=_confirm(sh),
                                      tool_log=tool_log, gap_s=gap_s, trace=trace,
                                      **({"scope": scope} if scope else {}))
            if hasattr(sh, "log_judged"):
                sh.log_judged(store, trace, session_id, platform, started)
            trace["logged"] = True
            evidence = sh.clean_user(user_message)
            if hit and hit.get("gap_s") is not None:
                lead = ("update pause continued" if hit.get("symptom", "").startswith("update pause")
                        else "backend stopped mid-turn; chat resumed")
                evidence = f"{lead} {hit['gap_s'] // 60} min later. {evidence}"
            sh.handle(hit, session_id, store, evidence=evidence, platform=platform, ask=user_message)
        except Exception as exc:
            log.debug("coach self_heal complaint: %s", exc)
            try:
                store.log({"event": "selfheal_judged", "session_id": session_id, "platform": platform,
                           "outcome": "error", "stage": "handle" if trace.get("logged") else "hook",
                           "error": f"{type(exc).__name__}: {exc}"[:300]})
            except Exception:
                pass

    def _selfheal_admission(session_id, reply, platform="", parent_session_id="", tool_log=None, ask="",
                            input_origin=""):
        try:
            sh = _load("self_heal")
            if not sh.user_facing(platform, parent_session_id,
                                  **({"input_origin": input_origin} if input_origin else {})):
                return
            if hasattr(sh, "agent_session") and sh.agent_session(session_id):
                return
            hit = sh.detect_admission(reply, llm=selfheal_llm, threshold=sh.audit_threshold(), tool_log=tool_log,
                                      ask=ask,
                                      confirm=_confirm(sh))
            # A machine signal carries what failed (tool, error line, count): lead with it, then the reply tail.
            evidence = (f"{hit['evidence'][:300]} | reply: {reply[-180:]}" if hit and hit.get("evidence")
                        else reply[-300:])
            sh.handle(hit, session_id, store, evidence=evidence, platform=platform, ask=ask)
        except Exception as exc:
            log.debug("coach self_heal admission: %s", exc)

    def _selfheal_guard(session_id, tool_name, args, result):
        """Lifecycle-guard false refusal (self_heal.guard_refusal_signal). Not gated on user_facing: a guard that
        refuses safe work is a defect whoever hit it (cron and Kanban sessions too). A `[selfheal-probe]` session
        is logged by handle() and files nothing."""
        try:
            sh = _load("self_heal")
            hit = sh.guard_refusal_signal(tool_name, args, result)
            if not hit:
                return
            a = args if isinstance(args, dict) else {}
            cmd = str(a.get("command") or a.get("script") or a.get("prompt") or "")
            if not sh.guard_occurrence_new(store, session_id or "", cmd, hit["shape"]):
                store.log({"event": "selfheal_skip", "why": "guard_repeat", "fingerprint": hit["fingerprint"],
                           "session_id": session_id})
                return  # the same refused command retried within GUARD_DEDUPE_S is one occurrence
            ask = coach.turn_context(session_id)[0] if session_id else ""
            sh.handle(hit, session_id, store, evidence=hit.get("evidence") or "", ask=ask)
        except Exception as exc:
            log.debug("coach self_heal guard refusal: %s", exc)

    def post_llm_call(session_id="", assistant_response=None, platform="", parent_session_id="", **_):
        try:
            if session_id:
                # The turn is over: a nudge still open got no checked continuation (failed turn, a continuation that
                # never reached pre_verify). Closed before the loop state is persisted below.
                text = assistant_response if isinstance(assistant_response, str) else ""
                why = ("turn_failed" if _load("reply_check").turn_failed(text) else
                       "empty_reply" if not text.strip() else "turn_ended")
                _close_open(session_id, why, why)
                coach.hint_turn_end(session_id, text, valid=why == "turn_ended")
        except Exception as exc:
            log.debug("coach post_llm_call close nudges: %s", exc)
        try:
            if session_id and _pv_seen.get(session_id) != coach.turn_number(session_id):
                text = assistant_response if isinstance(assistant_response, str) else ""
                _skipped(session_id, platform,
                         "turn_failed" if _load("reply_check").turn_failed(text) else
                         "empty_reply" if not text.strip() else "pre_verify_not_called")
                _pv_seen[session_id] = coach.turn_number(session_id)  # once per turn
        except Exception as exc:
            log.debug("coach post_llm_call skip: %s", exc)
        try:
            if session_id and isinstance(assistant_response, str) and assistant_response.strip():
                ask, log_lines = coach.turn_context(session_id)
                log_lines = list(log_lines or [])[-30:]
                _selfheal_run(_selfheal_admission, session_id, assistant_response, platform, parent_session_id,
                              log_lines, ask, str(_.get("input_origin") or ""))
                coach.turn_end(session_id, assistant_response)
                coach.loop_state(session_id)["last_ask"] = coach.turn_context(session_id)[0][:1500]
                loop = {k: v for k, v in coach.loop_state(session_id).items() if k not in ("diff_reviewed", "outcome_nudge")}
                store._update(session_id, lambda s: s.__setitem__("loop", loop))
        except Exception as exc:
            log.debug("coach post_llm_call: %s", exc)

    # session -> coach turn number of its last attempt-0 pre_verify, so post_llm_call can tell a turn the host never
    # handed to the reply check (an error exit, an interrupted turn) from one it checked. Process-local on purpose:
    # pre_verify and post_llm_call of one turn run in the same process.
    _pv_seen: dict = shared.setdefault("pv_seen", {})  # shared across a reload: the same turn's post_llm_call

    def _skipped(session_id, platform, reason, **extra):
        """Log why a turn got no reply check (`reply_skipped`). Cron turns are never checked by design: not logged."""
        if platform in ("cron",):
            return
        try:
            store.log({"event": "reply_skipped", "session_id": session_id or "", "platform": platform or "",
                       "reason": reason, "turn": coach.turn_number(session_id) if session_id else None, **extra})
        except Exception as exc:
            log.debug("coach reply_skipped: %s", exc)

    def _report_only_confirm(rc, session_id, ask, reply, prev, ref=None, via=None):
        """The report_only second opinion for `reply`, shared by pre_verify's nudge check and _nudge_outcome's
        re-check of the revision (agent-e5b6dcf428: the outcome used to skip it and file on the screen alone).
        Returns confirm() -> (p, evidence) | None: the strong model (aux task coach_report_only) with the turn's real
        tool arguments; None = not made (off, daily cap reached, error). Both callers share one daily cap
        (COACH_REPORT_ONLY_CONFIRM_CAP, counted over report_only_confirm events); `via` marks the outcome's rows."""
        tag = {"via": via} if via else {}

        def confirm():
            if os.environ.get("COACH_REPORT_ONLY_CONFIRM", "1").strip().lower() in ("0", "off", "false", "no"):
                return None
            try:
                cap = int(os.environ.get("COACH_REPORT_ONLY_CONFIRM_CAP", rc.CONFIRM_DAILY_CAP))
            except ValueError:
                cap = rc.CONFIRM_DAILY_CAP
            day0 = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            used = sum(1 for r in store.read_log(since=day0) if r.get("event") == "report_only_confirm")
            if used >= cap:
                store.log({"event": "report_only_confirm_capped", "session_id": session_id, "used": used, "cap": cap,
                           **tag})
                return None
            board = rc.named_card_facts(reply, coach.turn_rich(session_id), user_ask=ask) if hasattr(rc, "named_card_facts") else []
            raw = rc.aux_confirm(rc.confirm_system(), rc.confirm_prompt(ask, coach.turn_rich(session_id), reply,
                                                                        prev_turn=prev, board=board))
            res = rc.parse_confirm(raw)
            store.log({"event": "report_only_confirm", "session_id": session_id, "ok": res is not None,
                       "p": res[0] if res else None, "evidence": res[1] if res else "", **tag,
                       **({"board": [b.strip()[:200] for b in board]} if board else {}),
                       **((ref() if callable(ref) else ref) or {})})
            return res
        return confirm

    def pre_verify(session_id="", platform="", attempt=0, final_response="", changed_paths=None, turn_origin=None,
                   **_):
        """Check the agent's final answer before the turn ends; a hit keeps the turn going with a nudge.
        At most one nudge per turn (attempt 0 only); fail-open on every error.

        Runs inside one latency budget (reply_check.Budget) that fits the host's deadline for this callback. A nudge
        counts only when it is returned before that deadline: `reply_nudge` is logged then, `reply_nudge_abandoned`
        when the host already gave up (its result is discarded). The nudge is `reply_nudge_delivered` once the
        continuation it asked for reaches pre_verify again in the same turn (attempt >= 1), and
        `reply_nudge_undelivered` when the next check comes from a later turn instead.

        A fork turn (`turn_origin` set: background review, side question) shares the parent's session_id but its
        output never reaches the user: it is not judged and leaves the session's pending nudge alone
        (agent-0fe2ee028b). Core itself skips pre_verify for fork turns (fork commit "skip the pre_verify
        user-reply gate for fork turns"); this is the plugin-side guard for any host that passes the marker."""
        if turn_origin:
            return None
        if session_id and not attempt:
            _pv_seen[session_id] = coach.turn_number(session_id)
        try:
            rc = _load("reply_check")
            budget = rc.start_budget()
        except Exception as exc:
            log.debug("coach pre_verify budget: %s", exc)
            _skipped(session_id, platform, f"error:budget:{type(exc).__name__}")
            return None
        token = rc.use_budget(budget)
        try:
            if session_id:
                _nudge_delivery(session_id, attempt, final_response)
            out: dict = {}
            res = _reply_check(session_id, platform, attempt, final_response, changed_paths, out)
            if not (isinstance(res, dict) and res.get("action") == "continue"):
                return res
            rec = out.get("rec") or out.get("logged_rec") or {"event": "reply_nudge", "session_id": session_id,
                                                              "rule": out.get("rule", "")}
            rec.setdefault("nudge_id", _new_nudge_id())
            timing = {"elapsed_s": round(budget.elapsed(), 2), "budget_s": budget.own_s,
                      "host_timeout_s": budget.host_timeout_s}
            if budget.abandoned():
                st = coach.loop_state(session_id)
                if (st.get("outcome_nudge") or {}).get("nudge_id") == rec["nudge_id"]:
                    st.pop("outcome_nudge", None)  # no continuation will come for it
                store.log({**rec, "event": "reply_nudge_abandoned", **timing})
                return None
            if out.get("rec"):
                store.log({**rec, **timing})
                if not rec.get("outcome"):  # no judge re-checks this rule's revision: its outcome is known now
                    _log_outcome(session_id, rec, "nudge_outcome_untracked", reason="rule_not_judged")
            _park_nudge(session_id, rec, final_response)
            return res
        except Exception as exc:
            log.debug("coach pre_verify: %s", exc)
            return None
        finally:
            rc.reset_budget(token)

    def _nudge_delivery(session_id, attempt, final_response=None):
        """Close this session's open nudges at a reply check: delivered when the check is the nudge's continuation
        (attempt >= 1, or attempt 0 of the same turn on a different reply: core restarts its verify attempts after a
        mid-turn compaction); undelivered when the check belongs to a later turn. The same draft checked again at
        attempt 0 is not a continuation yet."""
        try:
            p = _pending(session_id)
            if not p:
                return
            turn, sig = coach.turn_number(session_id), _sig(final_response)
            for nid, n in list(p.items()):
                if n.get("turn") == turn:
                    if not attempt and (not n.get("sig") or n["sig"] == sig):
                        continue  # a repeated attempt-0 check of the nudged draft: no continuation yet
                    p.pop(nid, None)
                    _log_delivery(session_id, n, "reply_nudge_delivered", via="continuation" if attempt else "recheck")
                else:
                    p.pop(nid, None)
                    _log_delivery(session_id, n, "reply_nudge_undelivered", reason="next_turn")
        except Exception as exc:
            log.debug("coach nudge delivery: %s", exc)

    def _reply_check(session_id, platform, attempt, final_response, changed_paths, out):
        """pre_verify's check. The reply_nudge record of a judged hit goes to out["rec"] (logged by the caller only
        if the nudge is returned in time); out["rule"] names the source of any other continue."""
        try:
            if session_id and final_response and platform not in ("cron",):
                sink: dict = {}
                msg = _envelope(session_id, "reply", final_response, may_hold=not attempt, sink=sink)
                if msg:
                    out["rule"] = (sink.get("rec") or {}).get("rule") or "compute_envelope"
                    if sink.get("rec"):
                        out["logged_rec"] = sink["rec"]
                    return {"action": "continue", "message": msg}
            if session_id and final_response and platform not in ("cron",):
                _nudge_outcome(session_id, final_response, attempt, platform=platform)
            if attempt or platform in ("cron",):
                return None
            if not session_id or not final_response:
                _skipped(session_id, platform, "no_session" if not session_id else "empty_reply")
                return None
            rc = _load("reply_check")
            cfg = rc.settings()
            # agent-9382d717aa: a subagent this session spawned a turn earlier and still running is a started fix the
            # judges can't see in this turn's log; add core's live-registry line (once) before any judge reads it.
            coach.note_live_subagents(session_id)
            ask, log_lines = coach.turn_context(session_id)
            # this session's Kanban tools can't read the board: kanban_create fails too, so nudges don't ask for it
            kanban_down = coach.kanban_db_down(session_id) or rc.kanban_db_down(log_lines)
            so = _load("second_opinion")
            # unverified + this turn changed a git repo -> one bounded diff review on the strong model. The rule
            # joins the judged set only for such turns, so diff_review off (default) = the old behaviour exactly.
            # turn_log keeps 140 chars of a command, which can cut `cd <long path> && git commit` before the commit,
            # so the commit dirs come from the uncut commit commands too.
            repos = (so.changed_repos(log_lines + coach.turn_commit_lines(session_id), changed_paths)
                     if so.setting("diff_review") and "unverified" in rc.prefilter(final_response)
                     and coach.loop_state(session_id).get("diff_reviewed") != coach.turn_number(session_id) else {})
            only = list(cfg["rules"]) + (["unverified"] if repos and "unverified" not in cfg["rules"] else [])
            # agent-ee1b1162bc: a subagent reports to its parent agent, whose own final reply is judged; report_only
            # (incl. unverified_owner) and status_only don't hold the child. Its other rules still run.
            sub = platform == "subagent" or rc.is_subagent(session_id)
            if sub:
                only = [r for r in only if r not in rc.SUBAGENT_EXEMPT_RULES]
            if not only:
                _skipped(session_id, platform, "rules_off")
                return None
            th_unv = so.setting("diff_review_threshold")
            thresholds = dict(cfg.get("thresholds") or {})
            if repos and th_unv is not None:
                thresholds["unverified"] = th_unv
            s1 = _load("system_one")
            m = s1.mode()
            screen = ((lambda cands: s1.reply_screen(cands, ask, log_lines, final_response, rc.profile_text()))
                      if m != "off" else None)
            judged = {}
            # a no-tool follow-up is judged with the checks of the turn before it (agent-1f34642d7f)
            prev = coach.prev_turn(session_id)

            def draft_ref() -> dict:
                """Store the judged draft once per turn (drafts.py: redacted, capped) and return the field that links a
                reply_nudge / report_only_confirm event to it. Empty when the store is off or fails."""
                if "id" not in judged:
                    try:
                        dr = _load("drafts")
                        judged["id"] = dr.Drafts(store.root, clock=store.clock).put(
                            dr.new_id(), session_id=session_id, ask=ask, draft=final_response, platform=platform)
                    except Exception as exc:
                        log.debug("coach drafts: %s", exc)
                        judged["id"] = None
                return {"draft_id": judged["id"]} if judged.get("id") else {}

            confirm = _report_only_confirm(rc, session_id, ask, final_response, prev, ref=draft_ref)

            hit = rc.check(final_response, ask, log_lines, rc.aux_judge, threshold=cfg["threshold"],
                           only=only, changed_paths=changed_paths,
                           wake=lambda: rc.wake_sources(session_id, log_lines),
                           screen=screen, screen_below=s1.below(), shadow=m == "shadow", log=s1.log,
                           confirm=confirm, confirm_floor=rc.CONFIRM_FLOOR,
                           human_confirm_floor=rc.human_confirm_floor(platform),  # Desktop non-notice: 0.60
                           kanban_down=kanban_down,
                           # a Kanban worker turn has no user to push back: a capped second opinion skips the nudge
                           capped_nudge=platform != "kanban",
                           human_asks=lambda: rc.session_human_asks(session_id),
                           audit=lambda event: store.log({**event, "session_id": session_id,
                                                          "turn": coach.turn_number(session_id),
                                                          "notice": rc.notice_turn(ask), "platform": platform}),
                           report_only_fallback=cfg.get("report_only_fallback", rc.REPORT_ONLY_FALLBACK),
                           prev_turn=prev, platform=platform,
                           # the user asked again while the last reply relayed an unfinished owner: always get the
                           # report_only second opinion on this reply (relay_repeat.py, agent-1406d53913)
                           relay_repeat=(coach.loop_state(session_id).get("relay_repeat_turn")
                                         == coach.turn_number(session_id)),
                           **({"thresholds": thresholds} if thresholds else {}))
            if hit and hit["rule"] == "unverified" and repos:
                msg = _diff_review(session_id, so, hit, ask, log_lines, changed_paths, final_response, repos)
                if msg:
                    out["rule"] = "diff_review"
                    return {"action": "continue", "message": msg}
                if "unverified" not in cfg["rules"]:  # added only for the review: fall back to the next real rule
                    v = hit["verdict"]
                    rest = [r for r in cfg["rules"] if r in rc.prefilter(final_response)
                            and not (sub and r in rc.SUBAGENT_EXEMPT_RULES)
                            and v.get(r, 0) >= rc.rule_threshold(r, v, cfg.get("thresholds") or {}, cfg["threshold"])
                            and r != "unwatched" and not (r == "report_only" and rc.report_only_guard(ask, final_response, log_lines))]
                    hit = None
                    if rest:
                        r = max(rest, key=lambda x: v[x])
                        hit = {"rule": r, "p": v[r], "evidence": v["evidence"],
                               "nudge": rc.nudge_for(r, v["evidence"], kanban_down=kanban_down,
                                                     card_closed=rc.card_closed(log_lines), platform=platform)}
            if not hit:
                guard = rc.report_only_guard(ask, final_response, log_lines)
                _el = rc.budget_elapsed()
                store.log({"event": "reply_checked", "session_id": session_id, "platform": platform,
                           "candidates": rc.prefilter(final_response), "ask_len": len(ask or ""),
                           **({"elapsed_s": _el} if _el is not None else {}),
                           **({"guard": guard} if guard else {}), **({"subagent": True} if sub else {}),
                           **({"park": _park} if guard == "lifecycle_park" and (_park := rc.run_end_tool(log_lines))
                              else {})})
                return None
            tracked = hit["rule"] in rc.RULES
            # agent-689886b70b: a report_only hit after a successful park that ended this worker's own run can't be
            # acted on in this turn (every lifecycle call is refused): put the finding on the card for its next run
            _own = os.environ.get("HERMES_KANBAN_TASK", "") if platform == "kanban" else ""
            if hit["rule"] == "report_only" and (_ended := rc.own_run_ended(log_lines, _own)):
                res = rc.report_only_to_card(hit.get("evidence") or "", _own, reply=final_response)
                store.log({"event": "report_only_to_card", "session_id": session_id, "platform": platform,
                           "rule": hit["rule"], "p": hit["p"], "evidence": hit["evidence"], "run_end": _ended,
                           **res, **({"variant": hit["variant"]} if hit.get("variant") else {}), **draft_ref()})
                return None
            nid = _new_nudge_id()
            try:  # signals.same_item: ids of the paragraph the evidence quotes (agent-61b50f34ae)
                item_ids = rc.evidence_ids(final_response, hit.get("evidence") or "", tool_log=log_lines,
                                           user_ask=ask)
            except Exception:
                item_ids = []
            out["rec"] = {"event": "reply_nudge", "session_id": session_id, "rule": hit["rule"], "nudge_id": nid,
                          "p": hit["p"], "evidence": hit["evidence"], "ask_len": len(ask or ""),
                          # signals.template_holds groups on it: one rule holding the same fixed text in many chats.
                          # A resumed turn's core preamble is stripped first: its opening is shared by unrelated asks
                          "ask_head": " ".join(rc.strip_preamble(ask or "").split())[:40], "platform": platform,
                          **({"ask_wrapped": True} if rc.strip_preamble(ask or "") != (ask or "") else {}),
                          # ...and counts distinct prompts by it: one notice fanned out to N chats is one prompt
                          "ask_fp": hashlib.sha1((ask or "").encode("utf-8", "replace")).hexdigest()[:10],
                          **({"reason": hit["reason"]} if hit.get("reason") else {}),
                          **({"variant": hit["variant"]} if hit.get("variant") else {}), **draft_ref(),
                          **({"asks": hit["asks"]} if "asks" in hit else {}),  # denied_ask: which cards (agent-5c9b041010)
                          **({"item_ids": item_ids} if item_ids else {}),
                          # a nudge after a successful run-ending Kanban call: the worker can only reword now
                          # (agent-b04f510c13); counted so such nudges are measured, not just felt
                          **({"nudge_after_run_end": _run_end} if (_run_end := rc.run_end_tool(log_lines)) else {}),
                          **({"outcome": True} if tracked else {})}
            lead = rc.direct_lead(final_response)  # agent-e868df5c84: the draft's direct answer, kept in the nudge
            if lead:
                out["rec"]["lead"] = lead
                hit["nudge"] = rc.keep_answer(hit["nudge"], final_response)
            if tracked:  # judged again on attempt 1 (any rule): self-heal files the outcome, not the nudge
                st = coach.loop_state(session_id)
                old = st.get("outcome_nudge")
                if old:  # an earlier nudge of this turn still waiting for its revision: it never gets one now
                    _log_outcome(session_id, old, "nudge_outcome_lost", reason="superseded")
                st["outcome_nudge"] = {
                    "turn": coach.turn_number(session_id), "rule": hit["rule"], "ask": ask, "nudge_id": nid,
                    "at": time.time(), "sig": _sig(final_response),
                    "log": list(log_lines)[-25:], "kanban_down": kanban_down,
                    "threshold": cfg["threshold"], "thresholds": thresholds, "platform": platform, **draft_ref(),
                    **({"lead": lead} if lead else {})}
            return {"action": "continue", "message": hit["nudge"]}
        except Exception as exc:
            log.debug("coach pre_verify: %s", exc)
            if not attempt:
                _skipped(session_id, platform, f"error:{type(exc).__name__}", detail=str(exc)[:200])
            return None

    # The host bounds this callback with its own budget (cores that honour per-callback budgets; others ignore it)
    try:
        pre_verify.hermes_hook_timeout = _load("reply_check").pre_verify_host_timeout_s()
    except Exception as exc:
        log.debug("coach pre_verify host timeout: %s", exc)

    def _nudge_outcome(session_id, final_response, attempt=1, platform=""):
        """The continuation's check after this turn's reply_check nudge (any rule; attempt >= 1, or attempt 0 on a
        different reply after a mid-turn compaction): re-run that rule once on the revised reply and the turn's tool
        log (same screen judge, thresholds and, for report_only, the same strong-model second opinion and daily cap as
        the nudge: agent-e5b6dcf428), log a reply_nudge_outcome, never nudge again. Fail-open, but a nudge taken
        off the loop state always gets its outcome record. Self-heal files the outcome (signals.collect_coach), not
        the nudge (agent-e278947864, agent-3ba1979f34)."""
        n = None
        try:
            st = coach.loop_state(session_id)
            n = st.get("outcome_nudge")
            if not n:
                return
            if n.get("turn") != coach.turn_number(session_id):
                st.pop("outcome_nudge", None)
                _log_outcome(session_id, n, "nudge_outcome_lost", reason="turn_changed", nudge_turn=n.get("turn"),
                             turn=coach.turn_number(session_id))
                return
            if not attempt and (not n.get("sig") or n["sig"] == _sig(final_response)):
                n = None
                return  # the nudged draft checked again: its revision has not come yet
            st.pop("outcome_nudge", None)  # once per nudge, whatever happens below
            rule = n.get("rule") or "handoff"
            rc = _load("reply_check")
            ask, log_lines = coach.turn_context(session_id)
            log_lines = log_lines or n.get("log") or []
            ask = ask or n.get("ask") or ""
            prev = coach.prev_turn(session_id)
            plat = platform or n.get("platform") or ""
            cfg = rc.settings()
            nid_ref = {"nudge_id": n["nudge_id"]} if n.get("nudge_id") else {}
            res = rc.nudge_outcome(rule, final_response, ask, log_lines, rc.aux_judge,
                                   threshold=n.get("threshold", 0.7), thresholds=n.get("thresholds") or None,
                                   wake=lambda: rc.wake_sources(session_id, log_lines),
                                   kanban_down=n.get("kanban_down"),
                                   human_asks=lambda: rc.session_human_asks(session_id),
                                   prev_turn=prev,
                                   confirm=_report_only_confirm(rc, session_id, ask, final_response, prev,
                                                                ref=nid_ref, via="outcome"),
                                   confirm_floor=rc.CONFIRM_FLOOR, human_confirm_floor=rc.human_confirm_floor(plat),
                                   report_only_fallback=cfg.get("report_only_fallback", rc.REPORT_ONLY_FALLBACK))
            try:
                out_ids = (rc.evidence_ids(final_response, res.get("evidence") or "", tool_log=log_lines,
                                          user_ask=ask)
                           if res.get("evidence") else [])
            except Exception:
                out_ids = []
            _log_outcome(session_id, n, "reply_nudge_outcome", p=res["p"], resolved=res["resolved"],
                         judged=res["judged"], evidence=res["evidence"], reply=(final_response or "")[:300],
                         **({"item_ids": out_ids} if out_ids else {}),
                         **({"asks": res["asks"]} if "asks" in res else {}),
                         **({"src": res["src"]} if "src" in res else {}),
                         **({"unconfirmed": True} if res.get("unconfirmed") else {}),
                         **({} if attempt else {"via": "recheck"}))
            if n.get("lead") and rc.answer_dropped(n["lead"], final_response):
                # agent-e868df5c84: the nudge took the draft's direct answer out of the reply the user sees
                store.log({"event": "reply_nudge_answer_dropped", "session_id": session_id, "rule": rule,
                           **_ids(n), "lead": n["lead"], "reply": (final_response or "")[:300],
                           **({"platform": plat} if plat else {})})
            n = None
        except Exception as exc:
            log.debug("coach nudge outcome: %s", exc)
            try:
                store.log({"event": "reply_nudge_outcome_skipped", "session_id": session_id,
                           "reason": f"error:{type(exc).__name__}", "detail": str(exc)[:200],
                           **(_ids(n) if n else {})})
                if n and "outcome_nudge" not in coach.loop_state(session_id):
                    # taken off but not judged: its outcome record says so; resolved=None files it in
                    # signals.collect_coach as a coach-health defect, not the agent's (agent-614ed52ae7)
                    _log_outcome(session_id, n, "reply_nudge_outcome", p=None, resolved=None, judged=False,
                                 evidence=f"outcome check error: {type(exc).__name__}",
                                 reply=(final_response or "")[:300])
            except Exception:
                pass

    hooks = {"pre_final_gate": goal_gate, "pre_final_gate:compute_envelope": envelope_gate,
                   "pre_tool_call": pre_tool_call, "pre_verify": pre_verify, "post_llm_call": post_llm_call, "pre_llm_call": pre_llm_call, "post_tool_call": post_tool_call,
                   "transform_tool_result": transform_tool_result, "on_interim_message": on_interim_message}
    # Retirement removes the registered gate, not merely its enforcement mode.
    # Existing held callbacks still preserve the unresolved fallback on recheck.
    if _load("goal_disposition").settings()['mode'] == 'retired':
        hooks.pop("pre_final_gate")
    return coach, hooks


def register(ctx) -> None:
    store = _load("core").Store()
    _, hooks = build(store=store, live=_live_state(store.root))
    for name, fn in hooks.items():
        ctx.register_hook(name.split(":", 1)[0], fn)  # "event:label" = a second callback on the same event

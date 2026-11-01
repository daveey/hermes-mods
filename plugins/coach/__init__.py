"""coach: one nudge bus. Agent channel = hooks here (hints the model reads, invisible to the user).
User channel = dashboard/plugin_api.py + desktop-plugins/coach (one-button panels above the composer)."""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
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


def _load(name: str):
    """Load a sibling module once; re-exec it when the file changed, so a plugin reload
    (activate / discover_and_load(force=True)) picks up edits instead of the cached copy."""
    key = f"_coach_{name}"
    path = _HERE / f"{name}.py"
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    mod = sys.modules.get(key)
    if mod is None or getattr(mod, "_coach_mtime", None) != mtime:
        spec = importlib.util.spec_from_file_location(key, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[key] = mod
        spec.loader.exec_module(mod)
        setattr(mod, "_coach_mtime", mtime)
    return mod


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
    try:
        adopted_sessions = len(live.get("sessions") or {}) if live is not None else 0
        carried = _adopt(shared, coach, rules)
        if adopted_sessions:
            store.log({"event": "coach_state_adopted", "sessions": adopted_sessions, "open_nudges": carried})
    except Exception as exc:  # telemetry/schema faults must not erase the old build's live ownership
        log.debug("coach live state handover: %s", exc)
        coach.sessions = shared.setdefault("sessions", coach.sessions)

    authority_users: dict = shared.setdefault("authority_users", {})
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
        except Exception as exc:
            authority_users[session_id] = []
            log.debug("coach compute authority scope unavailable: %s", exc)
        try:
            # The figure comes from the assistant's ask; the authority from the user's own typed yes to it.
            authority_asked[session_id] = list(_load("compute_envelope").answered_asks(
                conversation_history, user_message, is_user_evidence=_load("goal_scope").is_evidence_row))
        except Exception as exc:
            authority_asked[session_id] = []
            log.debug("coach compute authority answered asks unavailable: %s", exc)
        try:
            if not session_id:
                return None
            goal_gate.capture(session_id=session_id, user_message=user_message,
                              conversation_history=conversation_history,
                              canonical_user_content=_.get("canonical_user_content"))
            # Seconds since this session's last recorded activity, read before user_turn bumps it: the length of
            # a backend stop when this turn is core's resume note (self_heal.resume_signal).
            last_seen = store.session(session_id).get("updated")
            rec = store.user_turn(session_id, user_message) or {}
            hint = coach.turn_start(session_id, user_message, platform=platform or "",
                                    parent_session_id=parent_session_id or "",
                                    replied_before=bool(_last_assistant(conversation_history)))
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
            threading.Thread(target=_lesson_bg, args=(session_id, user_message, prev), daemon=True).start()
            _selfheal_run(_selfheal_complaint, session_id, user_message,
                          prev or _last_assistant(conversation_history), platform, parent_session_id,
                          list(coach.loop_state(session_id).get("last_log") or []),
                          (store.clock() - last_seen) if isinstance(last_seen, (int, float)) else None)
            loop_hint = _fix_loop(session_id, user_message, turn)
            stall_hint = _stalled_goal(session_id, platform)
            hint = " ".join(h for h in (f"[coach] {hint}" if hint else "", loop_hint or "", stall_hint or "") if h)
            return {"context": hint} if hint else None
        except Exception as exc:
            log.debug("coach pre_llm_call: %s", exc)
            return None

    def post_tool_call(tool_name="", args=None, result=None, session_id="", status=None, duration_ms=0,
                       error_type=None, error_message=None, **_):
        try:
            goal_gate.observe(session_id=session_id, tool_name=tool_name, args=args, result=result,
                              status=status, tool_call_id=_.get("tool_call_id"))
            coach.observe(session_id, tool_name, args, result, status, duration_ms or 0, error_type, error_message)
        except Exception as exc:
            log.debug("coach post_tool_call: %s", exc)

    def transform_tool_result(tool_name="", result=None, session_id="", args=None, **_):
        try:
            coach.check_live(session_id, tool_name, args)
            coach.check_kanban_db_write(session_id, tool_name, args)
            coach.check_plan_first(session_id, tool_name, args)
            hint = coach.take(session_id)
            return rules.attach_hint(result, hint) if hint and isinstance(result, str) else None
        except Exception as exc:
            log.debug("coach transform_tool_result: %s", exc)
            return None

    # (session_id, where) already given the compute-envelope note
    envelope_told: set = shared.setdefault("envelope_told", set())

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

    def _envelope(session_id, where, text, may_hold=True, sink=None):
        """Compute-envelope check (compute_envelope.py). First hit per session/surface -> the note (the caller holds
        the block / the reply); a repeat after the note goes through and is logged as an agent fault. A reply hit is a
        reply_nudge with its own nudge_id (returned in `sink["rec"]` so the caller parks it for delivery); no judge
        re-checks the revision, so its outcome record (`nudge_outcome_untracked`) is written with it."""
        try:
            ce = _load("compute_envelope")
            if not ce.enabled():
                return None
            hit = (ce.authority(text, authority_users.get(session_id, ()), authority_asked.get(session_id, ()))
                   or ce.classify(text))
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

    def pre_tool_call(tool_name="", args=None, session_id="", **_):
        if tool_name in ("kanban_block", "kanban_create", "tool_call"):
            try:
                text = _load("compute_envelope").block_text(tool_name, args)
            except Exception as exc:
                log.debug("coach compute_envelope: %s", exc)
                text = ""
            msg = _envelope(session_id, "block", text) if text else None
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
                threading.Thread(target=fn, args=args, daemon=True).start()
        except Exception as exc:
            log.debug("coach self_heal: %s", exc)

    def _confirm(sh):
        """Second-stage judge: explicit `selfheal_confirm`, else (production, no injected judge) the configured
        `coach_defect_confirm` aux model, else none."""
        if selfheal_confirm != "auto":
            return selfheal_confirm
        return None if selfheal_llm else sh.confirm_judge()

    def _selfheal_complaint(session_id, user_message, prev, platform="", parent_session_id="", tool_log=None,
                            gap_s=None):
        try:
            sh = _load("self_heal")
            if not sh.user_facing(platform, parent_session_id):
                return
            hit = sh.detect_complaint(prev or "", user_message, llm=selfheal_llm, threshold=sh.threshold(),
                                      confirm=_confirm(sh),
                                      tool_log=tool_log, gap_s=gap_s)
            evidence = sh.clean_user(user_message)
            if hit and hit.get("gap_s") is not None:
                lead = ("update pause continued" if hit.get("symptom", "").startswith("update pause")
                        else "backend stopped mid-turn; chat resumed")
                evidence = f"{lead} {hit['gap_s'] // 60} min later. {evidence}"
            sh.handle(hit, session_id, store, evidence=evidence, platform=platform, ask=user_message)
        except Exception as exc:
            log.debug("coach self_heal complaint: %s", exc)

    def _selfheal_admission(session_id, reply, platform="", parent_session_id="", tool_log=None, ask=""):
        try:
            sh = _load("self_heal")
            if not sh.user_facing(platform, parent_session_id):
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

    def post_llm_call(session_id="", assistant_response=None, platform="", parent_session_id="", **_):
        try:
            if session_id:
                # The turn is over: a nudge still open got no checked continuation (failed turn, a continuation that
                # never reached pre_verify). Closed before the loop state is persisted below.
                text = assistant_response if isinstance(assistant_response, str) else ""
                why = ("turn_failed" if _load("reply_check").turn_failed(text) else
                       "empty_reply" if not text.strip() else "turn_ended")
                _close_open(session_id, why, why)
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
                              log_lines, ask)
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
                _nudge_outcome(session_id, final_response, attempt)
            if attempt or platform in ("cron",):
                return None
            if not session_id or not final_response:
                _skipped(session_id, platform, "no_session" if not session_id else "empty_reply")
                return None
            rc = _load("reply_check")
            cfg = rc.settings()
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

            def confirm():
                """report_only second opinion on the strong model (aux task coach_report_only) with the turn's real
                tool arguments. None = not made (off, daily cap reached, error): the screen's own p decides."""
                if os.environ.get("COACH_REPORT_ONLY_CONFIRM", "1").strip().lower() in ("0", "off", "false", "no"):
                    return None
                try:
                    cap = int(os.environ.get("COACH_REPORT_ONLY_CONFIRM_CAP", rc.CONFIRM_DAILY_CAP))
                except ValueError:
                    cap = rc.CONFIRM_DAILY_CAP
                day0 = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
                used = sum(1 for r in store.read_log(since=day0) if r.get("event") == "report_only_confirm")
                if used >= cap:
                    store.log({"event": "report_only_confirm_capped", "session_id": session_id, "used": used, "cap": cap})
                    return None
                raw = rc.aux_confirm(rc.confirm_system(), rc.confirm_prompt(ask, coach.turn_rich(session_id), final_response))
                res = rc.parse_confirm(raw)
                store.log({"event": "report_only_confirm", "session_id": session_id, "ok": res is not None,
                           "p": res[0] if res else None, "evidence": res[1] if res else "", **draft_ref()})
                return res

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
                                                     card_closed=rc.card_closed(log_lines))}
            if not hit:
                guard = rc.report_only_guard(ask, final_response, log_lines)
                store.log({"event": "reply_checked", "session_id": session_id, "platform": platform,
                           "candidates": rc.prefilter(final_response), "ask_len": len(ask or ""),
                           **({"guard": guard} if guard else {}), **({"subagent": True} if sub else {})})
                return None
            tracked = hit["rule"] in rc.RULES
            nid = _new_nudge_id()
            out["rec"] = {"event": "reply_nudge", "session_id": session_id, "rule": hit["rule"], "nudge_id": nid,
                          "p": hit["p"], "evidence": hit["evidence"], "ask_len": len(ask or ""),
                          # signals.template_holds groups on it: one rule holding the same fixed text in many chats
                          "ask_head": " ".join((ask or "").split())[:40], "platform": platform,
                          # ...and counts distinct prompts by it: one notice fanned out to N chats is one prompt
                          "ask_fp": hashlib.sha1((ask or "").encode("utf-8", "replace")).hexdigest()[:10],
                          **({"reason": hit["reason"]} if hit.get("reason") else {}),
                          **({"variant": hit["variant"]} if hit.get("variant") else {}), **draft_ref(),
                          **({"asks": hit["asks"]} if "asks" in hit else {}),  # denied_ask: which cards (agent-5c9b041010)
                          **({"outcome": True} if tracked else {})}
            if tracked:  # judged again on attempt 1 (any rule): self-heal files the outcome, not the nudge
                st = coach.loop_state(session_id)
                old = st.get("outcome_nudge")
                if old:  # an earlier nudge of this turn still waiting for its revision: it never gets one now
                    _log_outcome(session_id, old, "nudge_outcome_lost", reason="superseded")
                st["outcome_nudge"] = {
                    "turn": coach.turn_number(session_id), "rule": hit["rule"], "ask": ask, "nudge_id": nid,
                    "at": time.time(), "sig": _sig(final_response),
                    "log": list(log_lines)[-25:], "kanban_down": kanban_down,
                    "threshold": cfg["threshold"], "thresholds": thresholds, **draft_ref()}
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

    def _nudge_outcome(session_id, final_response, attempt=1):
        """The continuation's check after this turn's reply_check nudge (any rule; attempt >= 1, or attempt 0 on a
        different reply after a mid-turn compaction): re-run that rule once on the revised reply and the turn's tool
        log (same judge and thresholds), log a reply_nudge_outcome, never nudge again. Fail-open, but a nudge taken
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
            res = rc.nudge_outcome(rule, final_response, ask or n.get("ask") or "", log_lines, rc.aux_judge,
                                   threshold=n.get("threshold", 0.7), thresholds=n.get("thresholds") or None,
                                   wake=lambda: rc.wake_sources(session_id, log_lines),
                                   kanban_down=n.get("kanban_down"),
                                   human_asks=lambda: rc.session_human_asks(session_id))
            _log_outcome(session_id, n, "reply_nudge_outcome", p=res["p"], resolved=res["resolved"],
                         judged=res["judged"], evidence=res["evidence"], reply=(final_response or "")[:300],
                         **({"asks": res["asks"]} if "asks" in res else {}),
                         **({} if attempt else {"via": "recheck"}))
            n = None
        except Exception as exc:
            log.debug("coach nudge outcome: %s", exc)
            try:
                store.log({"event": "reply_nudge_outcome_skipped", "session_id": session_id,
                           "reason": f"error:{type(exc).__name__}", "detail": str(exc)[:200],
                           **(_ids(n) if n else {})})
                if n and "outcome_nudge" not in coach.loop_state(session_id):
                    # taken off but not judged: its outcome record says so, and resolved=None (not True) still
                    # files it in signals.collect_coach, as an outcome check that failed always did
                    _log_outcome(session_id, n, "reply_nudge_outcome", p=None, resolved=None, judged=False,
                                 evidence=f"outcome check error: {type(exc).__name__}",
                                 reply=(final_response or "")[:300])
            except Exception:
                pass

    return coach, {"pre_final_gate": goal_gate, "pre_final_gate:compute_envelope": envelope_gate,
                   "pre_tool_call": pre_tool_call, "pre_verify": pre_verify, "post_llm_call": post_llm_call, "pre_llm_call": pre_llm_call, "post_tool_call": post_tool_call,
                   "transform_tool_result": transform_tool_result}


def register(ctx) -> None:
    store = _load("core").Store()
    _, hooks = build(store=store, live=_live_state(store.root))
    for name, fn in hooks.items():
        ctx.register_hook(name.split(":", 1)[0], fn)  # "event:label" = a second callback on the same event

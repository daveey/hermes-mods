"""ask-gate: make it mechanically hard to ask David for something without checking first.

Layers (see README):
  L1 pre_llm_call        fresh block: status of every card linked to this chat + asks David already answered
  L2 pre_final_gate      judge every ask in a final reply BEFORE it is shown; hold + private nudge, or deliver
  L3 pre_tool_call       explicit human_request contract + evidence + judge; legacy human-ask prose needs Checked;
                         workers' kanban_complete / kanban_block handoffs are judged and leftovers become FYI lines
  L4 answered-asks ledger (sqlite, ~/.hermes/state) filled when David's message answers or dismisses an ask
Ordinary chat hooks fail open; explicit structured requests fail closed when validation/judging fails.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

try:
    from . import approval_adapter_loading, approval_reconciliation, lifecycle, user_step_lifecycle, runtime_contract, core, handoff, pr_approval_request, chat_delivery, judge as judge_mod
except ImportError:  # pytest/spec loading imports the hyphenated plugin dir without a parent package
    import importlib.util as _ilu
    import sys as _sys

    def _sib(name: str):
        key = "ask_gate_" + name
        if key in _sys.modules:
            return _sys.modules[key]
        spec = _ilu.spec_from_file_location(key, Path(__file__).with_name(name + ".py"))
        mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return _sys.modules.setdefault(key, mod)
    core, judge_mod, handoff = _sib("core"), _sib("judge"), _sib("handoff")
    lifecycle = _sib("lifecycle")
    user_step_lifecycle = _sib("user_step_lifecycle")
    approval_reconciliation = _sib("approval_reconciliation")
    approval_adapter_loading = _sib("approval_adapter_loading")
    runtime_contract = _sib("runtime_contract")
    pr_approval_request = _sib("pr_approval_request")
    chat_delivery = _sib("chat_delivery")

SKIP_PLATFORMS = {"cron", "kanban", "webhook", "subagent"}
DEFAULT_BOT_PROFILES = ("bb-tuner", "coach")  # cron profiles whose reports go to David and are judged like a chat reply
_SUBAGENTS: set[str] = set()                  # sessions seen with a parent_session_id (delegate_task children)
MAX_NUDGES = 2
LOG = core.TurnLog()
_LEDGER: dict[str, Any] = {}


def _off() -> bool:
    return bool(os.environ.get("ASK_GATE_OFF"))


def _is_worker() -> bool:
    return bool(os.environ.get("HERMES_KANBAN_TASK"))


def _chat_eligible(session_id: str, platform: str = "") -> bool:
    return bool(session_id) and not _off() and not _is_worker() and (platform or "").lower() not in SKIP_PLATFORMS


def _bot_profiles() -> tuple[str, ...]:
    """Profiles whose cron runs are gated: ASK_GATE_BOT_PROFILES (comma list), else config ``ask_gate.bot_profiles``."""
    env = os.environ.get("ASK_GATE_BOT_PROFILES")
    if env is not None:
        return tuple(x.strip() for x in env.split(",") if x.strip())
    try:
        from hermes_cli.config import load_config_readonly  # type: ignore
        got = (load_config_readonly().get("ask_gate") or {}).get("bot_profiles")
        if isinstance(got, (list, tuple)):
            return tuple(str(x) for x in got)
    except Exception:
        pass
    return DEFAULT_BOT_PROFILES


def _profile() -> str:
    env = os.environ.get("ASK_GATE_PROFILE")
    if env:
        return env
    try:
        from hermes_cli.profiles import current_profile_name  # type: ignore
        return current_profile_name("default") or "default"
    except Exception:
        pass
    for k in ("HERMES_PROFILE_NAME", "HERMES_PROFILE"):
        if os.environ.get(k):
            return os.environ[k]
    parts = core.home().parts
    return parts[parts.index("profiles") + 1] if "profiles" in parts and parts.index("profiles") + 1 < len(parts) else "default"


def _gate_eligible(session_id: str, platform: str = "") -> bool:
    """L2 runs for chats, and for platform-cron runs of a listed bot profile. Kanban workers, subagents and every
    other cron job stay skipped (core also runs this hook for them because it is registered include_background)."""
    if not session_id or _off() or _is_worker() or session_id in _SUBAGENTS:
        return False
    if (platform or "").lower() == "cron":
        return _profile() in _bot_profiles()
    return _chat_eligible(session_id, platform)


def ledger() -> "core.Ledger":
    path = os.environ.get("ASK_GATE_LEDGER") or str(core.home() / "state" / "ask-ledger.sqlite")
    if _LEDGER.get("path") != path:
        _LEDGER.update(path=path, obj=core.Ledger(Path(path)))
    return _LEDGER["obj"]


# ---- the model call ------------------------------------------------------------------------------------
def _model_cfg() -> tuple[str | None, str | None]:
    prov, model = os.environ.get("ASK_GATE_PROVIDER"), os.environ.get("ASK_GATE_MODEL")
    if prov or model:
        return prov, model
    try:  # an explicit auxiliary.ask_gate block in config.yaml wins; otherwise a cheap Claude model
        from hermes_cli.config import load_config_readonly  # type: ignore
        cfg = (load_config_readonly().get("auxiliary") or {}).get("ask_gate") or {}
        if cfg.get("model") or (cfg.get("provider") and cfg.get("provider") != "auto"):
            return None, None
    except Exception:
        pass
    return "anthropic", "claude-haiku-5-5"


def default_llm(system: str, user: str) -> str:
    prov, model = _model_cfg()
    return judge_mod.aux_llm(system, user, provider=prov, model=model)


LLM = default_llm  # tests replace this

_FALLBACK_CLASSES = ("fast", "standard", "deep")


def fallback_llms() -> list[tuple[str, Any]]:
    """Judges to retry an explicit ask on when the configured one is unreachable: class fast, then standard,
    then deep, each only when the routing policy has an eligible SUBSCRIPTION route for it (no paid fallback).
    Skips the class the configured judge already uses. Empty when routing is off or unreadable."""
    try:
        from agent.model_routing import eligible_routes  # type: ignore
        from hermes_cli.config import load_config_readonly  # type: ignore
        cfg = load_config_readonly()
        policy = cfg.get("model_routing") or {}
        if not policy.get("enabled"):
            return []
        own = ((cfg.get("auxiliary") or {}).get("ask_gate") or {}).get("model") or ""
    except Exception as e:
        core.log_event("judge_fallback_unknown", error=repr(e))
        return []
    out = []
    for level in _FALLBACK_CLASSES:
        selector = "class:" + level
        if selector == own:
            continue
        try:
            if not any(r.get("billing") == "subscription" for r in eligible_routes(policy, level)):
                continue
        except Exception:
            continue
        out.append((selector, lambda s, u, m=selector: judge_mod.aux_llm(s, u, provider="auto", model=m)))
    return out


FALLBACKS = fallback_llms  # tests replace this


def _llm():
    return LLM


# ---- verdict handoff to open-asks (it must not file a card for an unverified ask) ----------------------
def _verdict_path() -> Path:
    return core.home() / "cache" / "ask-gate" / "verdicts.json"


def record_verdict(session_id: str, verdict: str, text: str) -> None:
    p = _verdict_path()
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        d = {}
    d[session_id] = {"t": time.time(), "verdict": verdict, "h": core.text_hash(text)}
    d = {k: v for k, v in d.items() if time.time() - v.get("t", 0) < 86400}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d))
        os.replace(tmp, p)
    except OSError:
        pass


def last_verdict(session_id: str, max_age_s: float = 300) -> str | None:
    try:
        v = json.loads(_verdict_path().read_text()).get(session_id)
    except (OSError, ValueError):
        return None
    return v["verdict"] if v and time.time() - v.get("t", 0) <= max_age_s else None


# ---- shared: judge a set of ask sentences -------------------------------------------------------------
def _user_msgs(session_id: str) -> list[str]:
    msgs = LOG.users(session_id)
    if msgs:
        return msgs
    try:
        import sqlite3
        c = core._ro_connect(core.home() / 'state.db', timeout=2)
        rows = c.execute("select content from messages where session_id=? and role='user' order by id desc limit 30",
                         (session_id,)).fetchall()
        c.close()
        return [r[0] for r in reversed(rows) if isinstance(r[0], str)]
    except Exception:
        return []


def run_judge(session_id: str, asks: list[str], tools: list[dict], reply: str = "", *, structured: bool = False,
              llm=None) -> list[dict]:
    ids = core.card_ids(" ".join(asks) + " " + reply)
    statuses = core.card_status(ids)
    led = ledger().relevant(session_id, limit=20)
    retired = core.retired_matches(asks, ledger().retired_for(session_id, asks))
    remaining = [i for i in range(len(asks)) if i not in retired]
    if not remaining:
        return [retired[i] for i in range(len(asks))]
    ledger_text = "\n".join(f"- {a['topic']} -> {a['answer']} ({core.fmt_ts(a['ts'])})" for a in led)
    pending = [asks[i] for i in remaining]
    base = llm or _llm()
    caps = core.capability_sheet(force=True)
    tools_text = core.render_tools(tools)
    # A route counts as real when the sheet lists it or this turn already used it (a scope in a tool call).
    grounding = caps + "\n" + "\n".join(t["args"] for t in tools)
    if structured:
        # An explicit request is judged on the result its evidence cites, not on the 360-char excerpt of it.
        cited = core.render_evidence(" ".join(pending), tools)
        tools_text += ("\n\n" + cited) if cited else ""
    verdicts = judge_mod.judge(pending, tools_text=tools_text, user_msgs=_user_msgs(session_id),
                              card_status=statuses, ledger_text=ledger_text, caps=caps,
                              reply=reply, llm=judge_mod.structured_llm(base, grounding) if structured else base,
                              ledger=[a for a in led if not a.get('retired')],
                              soft=[core.is_soft_ask(a) for a in pending])
    for v in verdicts:
        i = remaining[v['i']]
        retired[i] = {**v, 'i': i}
    return [retired[i] for i in range(len(asks))]


# ---- L1 + L4 ------------------------------------------------------------------------------------------
_WRAP = re.compile(r"\[OUT-OF-BAND USER MESSAGE[^\]]*\]\s*(.*?)\s*\[/OUT-OF-BAND USER MESSAGE\]", re.S)


def _human_text(msg: str) -> str | None:
    """The part of a 'user' message David actually typed (None for tool notices / kanban completion pings)."""
    m = _WRAP.search(msg or "")
    if m:
        return m.group(1)
    s = (msg or "").strip()
    if not s or s.startswith("[IMPORTANT:") or re.match(r"^[✔✖⚠]\s*\[", s) or s.startswith("[SYSTEM"):
        return None
    return s


def pre_llm_call(session_id: str = "", user_message: str = "", conversation_history: list | None = None,
                 platform: str = "", parent_session_id: str = "", **_: Any) -> dict | None:
    if parent_session_id and session_id:
        _SUBAGENTS.add(session_id)
    if not _chat_eligible(session_id, platform) or parent_session_id:
        return None
    _prewarm()
    try:
        LOG.reset(session_id)
        hist = [m.get("content") for m in (conversation_history or []) if isinstance(m, dict) and m.get("role") == "user"
                and isinstance(m.get("content"), str)]
        users = [t for t in (_human_text(h) for h in hist + [user_message or ""]) if t]
        LOG.set_users(session_id, users)
        # Workflow state accepts the entire direct user message only. The legacy
        # answer extractor unwraps embedded text and is not provenance for a stop
        # or resume. Wrapped steering requires a trusted transport event adapter.
        directive = ledger().record_workflow_directive(session_id, user_message or "")
        if not directive:
            _record_answers(session_id, _human_text(user_message or ""), conversation_history or [],
                            raw_message=user_message or "")
        block = core.l1_block(session_id, ledger())
        return {"context": block} if block else None
    except Exception as e:  # never break a chat
        core.log_event("l1_error", error=repr(e))
        return None


def _last_assistant_text(history: list) -> str:
    for m in reversed(history):
        if isinstance(m, dict) and m.get("role") == "assistant" and isinstance(m.get("content"), str) and m["content"].strip():
            return m["content"]
    return ""


def _record_answers(session_id: str, msg: str | None, history: list,
                    raw_message: str | None = None) -> None:
    if not msg:
        return
    led = ledger()
    snapshot = led.pending_snapshot(session_id)
    open_asks = snapshot or []
    strong = core.is_strong_dismissal(msg)
    if snapshot is None:
        open_asks = core.find_asks(_last_assistant_text(history))
    direct_terminal = bool(re.fullmatch(
        r"(?:the|that|this) (?:subscription|account|step|action) "
        r"(?:is|was|has been) (?:cancelled|canceled|revoked|completed|impossible)"
        r"(?: on this platform)?[.!]?", msg.strip(), re.I))
    # Rendered steering wrappers are not a trusted transport provenance signal.
    # Require the complete direct input for durable terminal state; the legacy
    # extractor remains usable only for non-terminal conversational evidence.
    direct_input = raw_message is None or raw_message.strip() == msg.strip()
    if len(open_asks) == 1 and direct_terminal and direct_input:
        # A single pending question has an unambiguous target. Persist the actual
        # user's words, never a classifier-generated terminal assertion.
        led.add(open_asks[0], msg, session=session_id)
        led.set_open_asks(session_id, [])
        return
    if not open_asks and not strong:
        return
    if not (strong or core.looks_like_answer(msg)):
        return
    answered: list[dict] = []
    try:
        answered = judge_mod.classify_answer(msg, open_asks, _llm())
    except Exception as e:
        core.log_event("l4_classify_error", error=repr(e))
    if not answered and strong:  # model down or silent: a strong dismissal is still recorded
        topic = (open_asks[0] if open_asks else _last_assistant_text(history)[:160]) or "asks in general"
        answered = [{"i": -1, "topic": topic, "answer": msg[:160]}]
    for a in answered:
        led.add(a["topic"], msg, session=session_id, retire=False)
    if answered:
        answered_indices = {a.get("i") for a in answered if type(a.get("i")) is int and a["i"] >= 0}
        # A general dismissal (-1) is not proof that every distinct question was
        # answered. Keep unmatched pending items available for a later answer.
        led.set_open_asks(session_id, [ask for i, ask in enumerate(open_asks) if i not in answered_indices])
        core.log_event("l4_answered", session=session_id, n=len(answered))


# ---- tool log -----------------------------------------------------------------------------------------
def post_tool_call(tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "", **_: Any) -> None:
    if session_id:
        LOG.add(session_id, tool_name, args, result)


# ---- L2 -----------------------------------------------------------------------------------------------
# A refused ask (closed lifecycle, recorded terminal answer, stopped workflow, unreconcilable approval) is first
# HELD with a nudge naming the ask and why, so the agent resends without it. Only once the hold budget is spent
# is the reply replaced, and then by the draft minus the refused ask sentences plus one plain sentence on why.
# A bare "I withheld a request" notice is the last resort when the ask cannot be cut out cleanly.
_REFUSED_NOTE = ("(I left a request out of this reply: {why}. This note grants no new permission or "
                 "execution authorization.)")
_BARE_NOTICE = ("I withheld a request after checking authoritative approval or lifecycle state. "
                "No new permission or execution authorization is granted by this notice.")


def _plain_why(verdict: str, message: str) -> str:
    """One user-facing clause for the final note, from the refusal kind / adapter message."""
    m = (message or "").lower()
    if verdict == "workflow_stopped":
        return "you stopped this workflow"
    if verdict == "unverified":
        return "it did not pass the current-state check, so I am not asking you for it"
    if verdict == "retired" or "is retired" in m:
        return "you already gave a final answer on it"
    if verdict.startswith("user_step"):
        return ("that step is already closed" if "retired" in verdict
                else "its current state could not be checked, so I am not asking you for it")
    if "already approved" in m:
        return "that exact approval scope is already approved, so there is nothing to ask"
    if "is denied" in m:
        return "that exact approval scope was already denied"
    if "approval state could not be reconciled" in m:
        return "its current approval state could not be checked, so I am not asking you for it"
    return "its approval or lifecycle state could not be confirmed, so I am not asking you for it"


def _user_step_directives(text: str) -> list[str]:
    out = [m.group(0) for m in user_step_lifecycle._DIRECTIVE.finditer(text or "")]
    return out or [ln.strip() for ln in (text or "").splitlines() if user_step_lifecycle._MARKER.search(ln)]


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _has_ask(text: str, howto: bool) -> bool:
    return bool(core.find_asks(text, howto=howto) or handoff.find_handoffs(text))


def _strip_refused(text: str, refused: list[str], howto: bool, directives_only: bool) -> str | None:
    """The draft without the refused ask sentences, or None when they can't be cut out cleanly."""
    out = user_step_lifecycle._DIRECTIVE.sub("", text)
    for ask in sorted({a for a in refused if isinstance(a, str) and a.strip()}, key=len, reverse=True):
        out = out.replace(ask, "")
    lines = []
    for line in out.splitlines():
        if user_step_lifecycle._MARKER.search(line):
            continue
        if not directives_only and _has_ask(line, howto):
            kept = [s for s in _SENT_SPLIT.split(line) if not _has_ask(s, howto)]
            line = " ".join(kept)
        if line.strip() and not re.sub(r"[\s\-*•>#.,;:)(\d]+", "", line):
            continue  # only a dangling bullet / number left
        lines.append(line.rstrip())
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if user_step_lifecycle._MARKER.search(result):
        return None
    if not directives_only and _has_ask(result, howto):
        return None
    if any(len(a) > 20 and a in result for a in refused):
        return None
    return result


def _refuse(session_id: str, verdict: str, draft: str, refused: list[str], why: str,
            attempt: int, budget: int, howto: bool = False, directives_only: bool = False) -> dict:
    try:
        ledger().set_open_asks(session_id, [])
    except Exception as e:
        core.log_event("pending_clear_error", session=session_id, error=repr(e))
    reason = re.sub(r"^ask-gate:\s*", "", why or "").strip() or "Its current state could not be confirmed."
    if attempt < budget:
        try:
            record_verdict(session_id, "held", draft)
        except Exception:
            pass
        core.log_event("refused_held", session=session_id, verdict=verdict, attempt=attempt, asks=refused[:3])
        listed = "\n".join(f"- {a.strip()[:300]}" for a in refused[:5]) or "- (the request in your reply)"
        return {"hold": True, "nudge": (
            "ask-gate: your reply was NOT delivered. It asks David to:\n" + listed + "\n"
            f"Refused because: {reason}\n"
            "Resend the reply without that request. Keep your findings and results, and state the real blocker "
            "in plain words (what is stuck, and what you will do about it). Do not re-ask it in other words.")}
    try:
        record_verdict(session_id, verdict, draft)
    except Exception:
        pass
    note = _REFUSED_NOTE.format(why=_plain_why(verdict, why))
    try:
        kept = _strip_refused(draft, refused, howto, directives_only)
    except Exception as e:
        core.log_event("refused_strip_error", session=session_id, error=repr(e))
        kept = None
    core.log_event("refused_replaced", session=session_id, verdict=verdict, kept=bool(kept))
    if kept is None:
        return {"replace": _BARE_NOTICE + " " + note}
    return {"replace": (kept + "\n\n" + note) if kept else note}


def pre_final_gate(session_id: str = "", platform: str = "", model: str = "", final_response: str = "",
                   turn_messages: list | None = None, attempt: int = 0,
                   hold_budget: int | None = None, **_: Any) -> dict | None:
    if not _gate_eligible(session_id, platform) or not isinstance(final_response, str):
        return None
    budget = MAX_NUDGES if hold_budget is None else min(MAX_NUDGES, max(0, hold_budget))
    # Exact source reconciliation must not fail open when unrelated history or
    # audit storage is corrupt. Reporting is best-effort, suppression is not.
    exact = user_step_lifecycle.preflight({"session_id": session_id, "draft": final_response})
    if exact is not None:
        return _refuse(session_id, exact.get("reason") or "user_step", final_response,
                       _user_step_directives(final_response), exact.get("message", ""),
                       attempt, budget, directives_only=True)
    rejected = False
    try:
        users = _user_msgs(session_id)
        howto = bool(users) and core.asked_how_to(users[-1])
        asks = core.find_asks(final_response, howto=howto)
        retirement_candidates = asks + handoff.find_handoffs(final_response)
        if not howto and retirement_candidates and ledger().workflow_stopped(session_id):
            rejected = True
            return _refuse(session_id, "workflow_stopped", final_response, retirement_candidates,
                           "David stopped this workflow, so asking him to act on it is not allowed until he "
                           "resumes it himself.", attempt, budget, howto=howto)
        retired = core.retired_matches(retirement_candidates, ledger().retired_for(session_id, retirement_candidates))
        if retired:
            rejected = True
            refused = [retirement_candidates[i] for i in sorted(retired) if i < len(retirement_candidates)]
            return _refuse(session_id, "retired", final_response, refused or retirement_candidates,
                           "David already gave a terminal answer to this ask (revoked, cancelled, completed or "
                           "impossible). Do not ask again; this record does not authorize doing or reopening it.",
                           attempt, budget, howto=howto)
        reconciled = lifecycle.preflight({"session_id": session_id, "tool_name": "pre_final_gate",
                                          "draft": final_response, "howto": howto})
        if reconciled is None:
            reconciled = _reconcile_prose(session_id, "pre_final_gate", asks if howto else retirement_candidates)
        if reconciled is not None:
            rejected = True
            return _refuse(session_id, "approval_reconciled", final_response,
                           asks if howto else retirement_candidates, reconciled.get("message", ""),
                           attempt, budget, howto=howto)
        held = None if howto else _handoff_gate(session_id, final_response, turn_messages, attempt)
        if held is not None and attempt < budget:
            return held
        if held is not None:
            # A hold cannot protect a draft after the core retry budget ends.
            # Continue through the same rejection/ledger-clear path as the judge.
            verdicts = [{"i": 0, "verdict": "unverified", "reason": "Handoff was not opened."}]
        elif asks:
            tools = core.merge_tool_logs(core.tools_from_messages(turn_messages or []), LOG.get(session_id))
            # Re-read evidence even when the draft text has not changed.
            verdicts = run_judge(session_id, asks, tools, final_response)
        else:
            return None
        bad = judge_mod.bad(verdicts)
        rejected = bool(bad)
        core.log_event("verdict", session=session_id, attempt=attempt, asks=asks,
                       verdicts=[(v["verdict"], v.get("reason", "")) for v in verdicts])
        if not bad:
            ledger().set_open_asks(session_id, asks)
            record_verdict(session_id, "ok", final_response)
            return None
        if attempt < budget:
            record_verdict(session_id, "held", final_response)
            return {"hold": True, "nudge": judge_mod.nudge(asks, verdicts)}
        record_verdict(session_id, "unverified", final_response)
        # The judge's held asks still fail after the retry budget: drop them, keep the rest of the reply.
        return _refuse(session_id, "unverified", final_response, asks,
                       "It did not pass the current-state check.", budget, budget, howto=howto)
    except Exception as e:
        # Reporting/pending-ledger failures must not resurrect a rejected draft.
        # Preserve the existing fail-open policy only before a rejection exists.
        if rejected:
            return {"replace": "I withheld a request that did not pass the current-state check. "
                    "No action or approval is requested by this notice."}
        core.log_event("gate_error", session=session_id, error=repr(e))
        return None


def _handoff_gate(session_id: str, final_response: str, turn_messages: list | None, attempt: int) -> dict | None:
    """A reply that hands a terminal/password step to David (\"run `sudo ...` on the mini\", \"add the key to
    authorized_keys\") is refused unless this turn already opened a terminal for him: the agent must call
    run_in_user_terminal itself. Deterministic (no model call), so it holds even when the judge is down."""
    hits = handoff.find_handoffs(final_response)
    if not hits:
        return None
    tools = core.merge_tool_logs(core.tools_from_messages(turn_messages or []), LOG.get(session_id))
    if handoff.opened_terminal(tools):
        core.log_event("handoff_ok", session=session_id, hits=hits[:3])
        return None
    core.log_event("handoff_held", session=session_id, attempt=attempt, hits=hits[:3])
    if attempt < MAX_NUDGES:
        record_verdict(session_id, "held", final_response)
        return {"hold": True, "nudge": handoff.gate_nudge(hits)}
    return None  # nudged twice already: deliver (the normal ask judge still runs) rather than loop


# core may buffer streamed final text while this hook is registered, so a held draft never reaches the screen
pre_final_gate.needs_buffer = True  # type: ignore[attr-defined]
pre_final_gate.accepts_hold_budget = True  # type: ignore[attr-defined]
# core skips gate hooks in cron/kanban/subagent runs unless they opt in; _gate_eligible then lets only bot crons through
pre_final_gate.include_background = True  # type: ignore[attr-defined]


# ---- L3 -----------------------------------------------------------------------------------------------
CHECK_MSG = (
    "ask-gate: refused. This {what} says David has to act or decide, but it has no verified `Checked:` line. "
    "Check the premise first with a tool call in THIS turn (kanban_show for a card, the CLI/API that proves the "
    "state, token-broker scopes for a permission), then add a line `Checked: <tool or command you ran> — <one line "
    "of its output>` and retry. If a token-broker minter scope, ssh host or signed-in browser can do it, do it "
    "instead of asking. {extra}")
_WORKER_NO_ASK_KINDS = {"dependency", "time", "transient"}


def _tools_now(session_id: str) -> list[dict]:
    return LOG.get(session_id)


def _reconcile_prose(session_id: str, surface: str, asks: list[str]) -> dict | None:
    # This is candidate text, not a validated request or authority. Resolvers
    # classify the action and obtain exact scope/head from their own store.
    for ask in asks:
        context = {"session_id": session_id, "tool_name": surface,
                   "human_request": {"action": ask}}
        result = lifecycle.preflight(context) or approval_reconciliation.preflight(context)
        if result is not None:
            return result
    return None


RETIRED_MSG = ("ask-gate: this ask was retired by a recorded terminal user answer (revoked, cancelled, completed "
               "or impossible). Do not ask again; if the user explicitly reopened it, record the reopen first.")
UNKNOWN_RETIREMENT_MSG = ("ask-gate: retirement state is unavailable, so this ask is held. Park the work without "
                          "asking the user and retry once the ledger is readable.")


def _retired_block(session_id: str, asks: list[str]) -> dict | None:
    """Durable terminal answers precede judgment on structured ask surfaces; unknown state fails closed."""
    asks = [a for a in asks if isinstance(a, str) and a.strip()]
    if not asks or not session_id:
        return None
    try:
        retired = core.retired_matches(asks, ledger().retired_for(session_id, asks))
    except Exception as e:
        core.log_event("retirement_unknown", session=session_id, error=repr(e))
        return {"action": "block", "message": UNKNOWN_RETIREMENT_MSG}
    if retired:
        core.log_event("retired_structured_ask", session=session_id)
        return {"action": "block", "message": RETIRED_MSG}
    return None


# Process-local one-shot lint, not a new authorization or durable refusal policy.
# Do not reset on a turn boundary: an unchanged retry must pass this lint.
_PR_FILING_HELD: set[tuple[str, str, str]] = set()


def _pr_filing_hold(session_id: str, tool_name: str, args: dict, request: dict) -> dict | None:
    row = {"human_request": request, "title": args.get("title"), "body": args.get("body")}
    if not pr_approval_request.looks_like_pr_approval(row) or pr_approval_request.request_target(row) is not None:
        return None
    ask = json.dumps({k: request[k] for k in ("action", "scope")}, sort_keys=True)
    key = (session_id, tool_name, core.text_hash(ask))
    if key in _PR_FILING_HELD:
        return None
    _PR_FILING_HELD.add(key)
    return {"action": "block", "message":
            "ask-gate: PR approval wording held once. " + pr_approval_request.parse_failure_reason(row)
            + " Canonical wording: " + pr_approval_request.canonical_wording(row)
            + " Fill missing placeholders and the actual unblock/archive outcomes; do not broaden authorization. "
              "Retrying this same ask passes this wording check, but all existing checks still apply. "
              "This notice grants no approval and changes no task state."}


def _human_request_gate(session_id: str, tool_name: str, args: dict) -> dict | None:
    """Explicit asks fail closed without changing the ordinary chat/legacy prose gates."""
    try:
        request = core.validate_human_request(args["human_request"])
        kind = args.get("kind") if tool_name == "kanban_block" else args.get("block_kind")
        if kind in _WORKER_NO_ASK_KINDS:
            raise ValueError("human_request cannot accompany dependency/time/transient blocks")
        if tool_name == "kanban_create" and args.get("initial_status") != "blocked":
            raise ValueError("human_request requires initial_status=blocked on creation")
        filing = _pr_filing_hold(session_id, tool_name, args, request)
        if filing is not None:
            return filing
        context = {"session_id": session_id, "tool_name": tool_name,
                   "human_request": request, "args": args}
        reconciled = lifecycle.preflight(context) or approval_reconciliation.preflight(context)
        if reconciled is not None:
            return reconciled
        retired = _retired_block(session_id, [request["action"]])
        if retired is not None:
            return retired
        tools = _tools_now(session_id)
        evidence = request["evidence"]
        claim = core.checked_line(evidence) or evidence
        if not core.checked_matches(claim, tools):
            raise ValueError("human_request.evidence matches no tool call made this turn; verify the actual action and scope")
        # Keep the complete contract in the ask itself: prose detectors and their sentence/length
        # caps must not select an unrelated sentence or drop a late field from this explicit ask.
        text = json.dumps(request, ensure_ascii=False)
        verdicts = _judge_explicit(session_id, tool_name, text, tools)
        if verdicts is None:
            # Every reachable judge failed: no verdict exists. The deterministic checks above (schema,
            # evidence matching a tool call this turn, reconciliation, retirement) passed, so the ask goes
            # through visibly rather than turning into a hidden wait.
            core.log_event("l3_human_request", tool=tool_name, session=session_id, verdict="judge_unavailable")
            return None
        if not verdicts or judge_mod.bad(verdicts):
            raise ValueError("human_request was not verified: " + judge_mod.nudge([text], verdicts))
        core.log_event("l3_human_request", tool=tool_name, session=session_id, verdict="ok")
        return None
    except ValueError as e:  # a validation failure or a real verdict: the ask itself was refused
        core.log_event("l3_human_request_held", tool=tool_name, session=session_id, error=repr(e))
        return {"action": "block", "message": "ask-gate: human_request refused. " + str(e)
                + " Keep operational failures parked without a human_request; supply the actual action, why_human, "
                  "scope, evidence and interaction only when a human step is verified. Metadata is not authorization. "
                  "OS authentication is distinct from approval; use the supported user-terminal flow, never passwords in chat."}
    except Exception as e:  # the gate itself broke: say so, and never advise dropping the ask
        core.log_event("l3_human_request_gate_error", tool=tool_name, session=session_id, error=repr(e))
        return {"action": "block", "message": "ask-gate: could not check this human_request because the gate hit an "
                f"internal error ({type(e).__name__}). This is not a verdict on the ask: keep it, and retry the "
                "same call once. Do not park the card without it."}


def _judge_explicit(session_id: str, tool_name: str, text: str, tools: list[dict]) -> list[dict] | None:
    """Verdicts from the configured judge, else from the class fallbacks (fast, standard, deep; subscription
    only). None when no judge was reachable. A verdict, good or bad, is final: it never falls back."""
    errors = []
    try:
        return run_judge(session_id, [text], tools, text, structured=True)
    except judge_mod.JudgeUnavailable as e:
        errors.append(("configured", str(e)))
    for name, llm in FALLBACKS():
        try:
            verdicts = run_judge(session_id, [text], tools, text, structured=True, llm=llm)
        except judge_mod.JudgeUnavailable as e:
            errors.append((name, str(e)))
            continue
        core.log_event("judge_fallback", tool=tool_name, session=session_id, judge=name, errors=errors)
        return verdicts
    core.log_event("judge_unavailable", tool=tool_name, session=session_id, errors=errors)
    return None


def pre_tool_call(tool_name: str = "", args: Any = None, session_id: str = "", **_: Any) -> dict | None:
    if _off() or not isinstance(args, dict):
        return None
    try:
        if tool_name in {"clarify", "kanban_create", "kanban_block"} and session_id:
            is_ask = (tool_name == "clarify" or args.get("human_request") is not None
                      or core.needs_checked(tool_name, args, _is_worker())[0])
            if is_ask:
                try:
                    stopped = ledger().workflow_stopped(session_id)
                except Exception as e:
                    core.log_event("retirement_unknown", session=session_id, error=repr(e))
                    return {"action": "block", "message": UNKNOWN_RETIREMENT_MSG}
            else:
                stopped = False
            if stopped:
                return {"action": "block", "message": "ask-gate: workflow is terminal by explicit user stop. "
                        "Do not request action or execute/reopen it without an explicit workflow resume."}
        if tool_name == "clarify":
            questions = args.get("questions", [])
            candidates = [q["question"] for q in questions
                          if isinstance(q, dict) and isinstance(q.get("question"), str)] if isinstance(questions, list) else []
            reconciled = _reconcile_prose(session_id, tool_name, candidates) or _retired_block(session_id, candidates)
            if reconciled is not None:
                return reconciled
        delivery = _chat_delivery_gate(session_id, tool_name, args)
        if delivery is not None:
            return delivery
        if tool_name == "kanban_complete" and _is_worker():
            return _worker_complete(session_id, args)
        if tool_name not in ("kanban_create", "kanban_block"):
            return None
        if args.get("human_request") is not None:
            return _human_request_gate(session_id, tool_name, args)
        candidate = str(args.get("reason") or "") if tool_name == "kanban_block" else (
            str(args.get("title") or "") + "\n" + str(args.get("body") or ""))
        found = core.find_asks(candidate)
        reconciled = _reconcile_prose(session_id, tool_name, found)
        if reconciled is not None:
            return reconciled
        if tool_name == "kanban_block" and str(args.get("kind") or "") in _WORKER_NO_ASK_KINDS:
            return None
        reconciled = _retired_block(session_id, found)
        if reconciled is not None:
            return reconciled
        gated, text = core.needs_checked(tool_name, args, _is_worker())
        if not gated or "Filed automatically by the `open-asks` plugin" in text:
            return None
        what = "blocked card" if tool_name == "kanban_create" else "block"
        tools = _tools_now(session_id)
        claim = core.checked_line(text)
        if not claim:
            return {"action": "block", "message": CHECK_MSG.format(what=what, extra="(No `Checked:` line found.)")}
        if not core.checked_matches(claim, tools):
            return {"action": "block", "message": CHECK_MSG.format(
                what=what, extra=f"(Your Checked line \"{claim[:100]}\" matches no tool call made this turn.)")}
        sents = core.find_leftovers(text) + [a for a in core.find_asks(text) if a not in core.find_leftovers(text)]
        sents = sents or [str(args.get("title") or text)[:200]]
        verdicts = run_judge(session_id, sents[:6], tools, text)
        core.log_event("l3_verdict", tool=tool_name, session=session_id,
                       verdicts=[(v["verdict"], v.get("reason", "")) for v in verdicts])
        if judge_mod.bad(verdicts):
            return {"action": "block", "message": "ask-gate: refused after the judge read the card. "
                    + judge_mod.nudge(sents, verdicts).split("\n", 1)[1]}
        return None
    except Exception as e:
        core.log_event("l3_error", error=repr(e))
        return None


def _chat_delivery_gate(session_id: str, tool_name: str, args: dict) -> dict | None:
    """Refuse a human ask to deliver text into a Hermes chat: FINAL SEND / cross_session does that (agent-c25865e208).
    Deterministic, before any judge; fails open (a detector error never blocks the call)."""
    try:
        refused = chat_delivery.refused_targets(_tools_now(session_id)) if session_id else []
        ref = None
        request = args.get("human_request")
        if tool_name == "kanban_block" and str(args.get("kind") or "") not in _WORKER_NO_ASK_KINDS:
            ref = (chat_delivery.check_request(request, refused, str(args.get("reason") or "")) if request is not None
                   else chat_delivery.find_delivery(str(args.get("reason") or ""), refused=refused))
        elif tool_name == "kanban_create" and (args.get("initial_status") == "blocked" or request is not None):
            ref = (chat_delivery.check_request(request, refused, str(args.get("body") or "")) if request is not None
                   else chat_delivery.find_delivery(str(args.get("title") or "") + "\n" + str(args.get("body") or ""),
                                                    refused=refused))
        elif tool_name == "kanban_complete" and _is_worker():
            summary = str(args.get("summary") or args.get("result") or "")
            for s in core.find_leftovers(summary):
                if "FINAL SEND" in s.upper():
                    continue
                ref = chat_delivery.find_delivery(s, structured=True, refused=refused)
                if ref:
                    break
        if not ref:
            return None
        core.log_event("chat_delivery_ask_refused", tool=tool_name, session=session_id, ref=ref)
        return {"action": "block", "message": chat_delivery.refusal(ref, worker=_is_worker())}
    except Exception as e:
        core.log_event("chat_delivery_error", tool=tool_name, session=session_id, error=repr(e)[:300])
        return None


FYI_PREFIX = "FYI (unverified, not an ask for David): "


def _worker_complete(session_id: str, args: dict) -> dict | None:
    summary = str(args.get("summary") or args.get("result") or "")
    left = core.find_leftovers(summary)
    if not left:
        return None
    tools = _tools_now(session_id)
    verdicts = run_judge(session_id, left[:8], tools, summary)
    keep_ok = {left[v["i"]] for v in verdicts if v["verdict"] == "ok"}
    # an ask is only left standing if the judge approved it AND the handoff carries a matching Checked line
    claim = core.checked_line(summary)
    if claim and core.checked_matches(claim, tools):
        pass
    else:
        keep_ok = set()
    out = summary
    changed = False
    for s in left:
        if s in keep_ok or FYI_PREFIX in s:
            continue
        out = out.replace(s, FYI_PREFIX + s, 1)
        changed = True
    core.log_event("worker_handoff", session=session_id, leftovers=left, rewritten=changed)
    if not changed:
        return None
    key = "summary" if args.get("summary") else "result"
    return {"action": "modify", "args": {key: out}}


_PREWARMED = {"done": False}


def _prewarm() -> None:
    """The first aux call of a process can take 20+ s (client + credential-pool setup); core gives a gate hook 30 s.
    Pay that cost once, in the background, from the first chat turn: it must run inside the agent's context
    (profile-scoped secrets are context-bound), so the thread runs a copy of the caller's contextvars."""
    if _PREWARMED["done"] or _off() or _is_worker() or os.environ.get("ASK_GATE_NO_PREWARM"):
        return
    _PREWARMED["done"] = True
    import contextvars
    import threading

    def go():
        try:
            prov, model = _model_cfg()
            judge_mod.aux_llm("Reply with the single word ok.", "ok", timeout=40.0, max_tokens=5, provider=prov, model=model)
        except Exception as e:
            core.log_event("prewarm_error", error=repr(e)[:300])
    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(go,), name="ask-gate-prewarm", daemon=True).start()


def register(ctx) -> None:
    runtime_contract.require_runtime_contract()
    approval_adapter_loading.install(ctx, approval_reconciliation)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("post_tool_call", post_tool_call)
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("pre_final_gate", pre_final_gate)

# session-outcome

Every non-archived chat should have exactly one stored outcome, and the sidebar paints from it rather than from
idleness. Core owns the storage and the derivation (`hermes_state_outcome`). This plugin lets the agent declare
the outcome and feeds core the two things only plugins know: open user decisions and a summary judge.

## Behavior

- **Tool `session_outcome`.** It is offered only in desktop, tui and cli chats (a no-cache `check_fn`; the handler
  also refuses other session sources), and only when core has `hermes_state_outcome`.
  - `{"kind": "done", "summary": "..."}` declares `{"kind": "done", "summary"}`.
  - `{"kind": "decide", "items": [{"question", "choices": [≥2 {"label", "value"}], "card_id"?}]}` writes each
    question into the user-steps ledger with `add_step` (a `choose` step whose labels become its buttons), then
    declares `{"kind": "decision", "items": [{id, question, choices, state: "open", card_id?}]}`. Nothing is
    written when any item is malformed.
  - `{"kind": "wait", "on": [{"kind": "card|process|cron|time|capacity|subagent", "id", "label"?, "state"?,
    "eta"?}]}` declares `{"kind": "waiting", "on": [...]}`.
- **Provider** `user-steps`: user-steps `open_steps(sid)` → `{"decision_items": [...], "waiting_on": []}`, or None
  when the chat has no open steps.
- **Judge**: is the last assistant message a closing summary? One call on the subscription pool
  (`pool-llm`'s `complete`, `anthropic`, `fast` tier) with a 3 s budget. YES → True, NO → False, anything else,
  any error or a timeout → None (core falls back to its heuristic). Verdicts are cached per (session, text).
- **`pre_final_gate`** (buffered): when the turn made no `session_outcome` call, core does not already say
  `decision`, `waiting` or `muted`, the chat has no open user-steps step, and the judge says the reply is NOT a
  summary, the reply is held once with a request for the call. The nudge carries `[session-outcome]`. When it is
  already in the turn (or this gate held earlier in the turn), the reply passes. It never asks twice in a turn.
  An undecided judge never holds.
- **No core**: without `hermes_state_outcome` the plugin loads, the tool is not offered, the gate passes and
  nothing registers.
- Off switch: `HERMES_SESSION_OUTCOME_OFF=1`. Kanban workers and subagents are skipped.

## Tests

`tests/test_session_outcome.py`, with a fake `hermes_state_outcome` that records calls (contract signatures):

- each tool kind: done, wait, decide, and the malformed cases (no summary, unknown wait kind, fewer than 2 choices,
  core rejection);
- decide writes ledger steps with options and choices, then declares;
- the provider returns open steps as decision items;
- the judge: YES/NO, timeout → None, failure or unclear answer → None, the default path calls pool `fast` tier;
- the gate asks once and passes the second miss (by nudge tag and by the in-memory backup), passes after a tool
  call, on a summary, on an undecided judge, and when a wait or an open step outranks;
- non-interactive sources (cron, slack, Kanban worker) get no tool and no gate; tui and cli do;
- the no-core no-op path.

User-steps' `open_steps`, `add_step` and the assent-echo filter are tested in
`plugins/user-steps/tests/test_outcome_helpers.py`.

```sh
uv run --with pytest python -m pytest -q -p no:cacheprovider plugins/session-outcome/tests
```

Untested: the real core wiring (core calls the provider and judge at refresh time), since `hermes_state_outcome` is
not on main yet.

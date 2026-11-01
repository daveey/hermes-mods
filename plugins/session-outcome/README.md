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
- **Judge**: is the last assistant message a closing summary? Desktop, tui and cli chats only: any other
  `source=` (core passes it), or a session row whose source is known and not interactive, gets None with no model
  call. One call on the subscription pool
  (`pool-llm`'s `complete`, `anthropic`, `fast` tier, thinking off, 8 tokens) with a 6 s budget. Thinking is
  off because the fast model's adaptive thinking spent the whole token budget and about two calls in three came
  back empty. The judge sees the user's latest real request (`<request>`, capped at 2,000 characters; kanban
  notices, auto-continue rows, this plugin's nudge and recovery re-prompts (`[False-stop recovery]`,
  `[Session babysitter]`, which only replay the event above them) are skipped) and the reply (`<message>`): without the
  request it judged a finished answer to "what should I ask?" as "a question". YES → True, NO → False, anything else,
  any error or a timeout → None (core falls back to its heuristic). Verdicts are cached per (session, text).
  The judge reads the whole reply up to 12,000 characters, else its head and tail around an omission marker:
  never only the tail, which hides a long report's answer and gets a finished report judged NO.
  Things the reply only describes as happening later on their own (a cron's next runs, a card that finishes by
  itself, a check a queued card will run even if it hasn't run yet) count as YES when the assistant owes nothing more; only the assistant's own promise to do more or report
  later is NO. Without that rule a full answer to "why is it not green?" that named the chat's cron got NO every time.
- **`pre_final_gate`** (buffered): when the turn made no `session_outcome` call, it asks core
  `derive_outcome(sid, ignore_live_turn=True, pending_reply=<the candidate reply>)`. Only
  `unresolved{no_summary}` holds the reply, once, with a request for the call. Core's own precedence decides, so
  card decisions, waits and mutes pass. Open user-steps steps do not: in the gate's derive the provider returns
  nothing, because user-steps resolves the steps a reply answers in its own `transform_llm_output`, which runs
  after this gate (counted, they let an undeclared reply pass as `decision`, and the chat fell to
  `unresolved{no_summary}` a second later when that reply resolved them). Inside the gate the judge gets a 15 s budget (6 s elsewhere): the gate runs in a
  busy Desktop serve process under a 30 s hook limit, with the reply already buffered. When the judge still gives
  up (timeout, error, no YES/NO) and only core's heuristic called the reply a summary, the gate also holds once.
  A later refresh whose judge works (the auditor's CLI `outcome-audit --refresh`) could otherwise flip that
  heuristic `done` to `unresolved{no_summary}`, and nobody would have been asked. Judge give-ups are logged at
  INFO (`session-outcome judge gave up for <sid> after Ns (gate)`). The nudge carries
  `[session-outcome]`. When it is already in the turn (or this gate held earlier in the turn), the reply passes. It
  never asks twice in a turn. A core whose `derive_outcome` lacks those keywords, or any error, passes.
- **Per-turn call record**: a call is recorded as (session, tool call id) and counts only for a gate whose turn
  holds that call id, so it never carries into the next turn.
- **The gate's turn starts at the last user row that is not its own nudge**, kanban notices included. Core's
  `turn_messages` starts at the last *typed* message, so a turn a notice started also carried the earlier turns:
  their `session_outcome` call let the notice turn's reply pass undeclared and unasked, and their nudge counted as
  "already asked".
- **`decide` is all or nothing**: every item is validated before any write; a failed write or a rejected
  declaration removes the steps this call added (pre-existing steps it matched are kept).
- **No core**: without `hermes_state_outcome` the plugin loads, the tool is not offered, the gate passes and
  nothing registers.
- Off switch: `HERMES_SESSION_OUTCOME_OFF=1`. Kanban workers and subagents are skipped.

## Tests

`tests/test_session_outcome.py`, with a fake `hermes_state_outcome` that records calls (contract signatures):

- each tool kind: done, wait, decide, and the malformed cases (no summary, unknown wait kind, fewer than 2 choices,
  core rejection);
- decide is all or nothing: a bad later item writes nothing; a failed write or a rejected declaration keeps nothing
  this call added;
- the call record is per turn (same turn passes, the next turn and another chat are asked);
- a notice-started turn ignores the earlier turns' call (by tool name and by call id) and nudge, and still counts
  its own call and nudge;
- the judge skips non-interactive sources (by `source=` and by session row) without a model call;
- decide writes ledger steps with options and choices, then declares;
- the provider returns open steps as decision items;
- the judge: YES/NO, timeout → None, failure or unclear answer → None, the default path calls pool `fast` tier;
- the gate holds once when the judge gives up (error, unclear answer, cached unclear answer, timeout) and only the
  heuristic said done, and the second miss passes; the gate's judge budget is longer than a refresh's; the gate's
  scope does not leak into a later refresh; a wait that outranks the judge still passes;
- the judge gets a long reply's head and tail (whole when short), wrapped in `<message>`;
- the judge gets the user's latest real request (synthetic rows and the nudge skipped, capped), or none;
- the default pool path turns thinking off (`pool-llm` `complete(thinking=...)` puts it in the Anthropic body);
- the gate asks once and passes the second miss (by nudge tag and by the in-memory backup), passes after a tool
  call, on a summary, on an undecided judge, and when a wait or a card's ask outranks; an open user-steps step
  does not vouch for the reply (a NO reply is held, a YES passes), and outside the gate the step still counts;
- non-interactive sources (cron, slack, Kanban worker) get no tool and no gate; tui and cli do;
- a Desktop chat's turn admitted via the gateway `local` platform (no bound source) gets the tool from its
  session row, the same predicate the gate uses, so the gate never asks for a call the chat cannot make;
- the no-core no-op path.

User-steps' `open_steps`, `add_step` and the assent-echo filter are tested in
`plugins/user-steps/tests/test_outcome_helpers.py`.

`tests/test_real_core.py` runs the gate against the REAL `hermes_state_outcome` from the session-outcome core
worktree (`HERMES_OUTCOME_CORE_DIR`, default `~/code/_wt/session-outcome`) on a temp HERMES_HOME state.db: a live
turn marker doesn't hide the reply, a non-summary is held once, a summary passes, an undecided judge falls back to
core's heuristic, and an open user-steps step makes core say `decision`. It skips without that checkout or core's
deps, so run it with the Hermes venv:

```sh
uv run --with pytest python -m pytest -q -p no:cacheprovider plugins/session-outcome/tests
~/.hermes/hermes-agent/venv/bin/python -m pytest -q -p no:cacheprovider plugins/session-outcome/tests
```

`tests/test_judge_live.py` replays the real prompt on the real pool fast tier (3 runs per case, all must agree):
replies that were misjudged (the scheduled-cron answer) plus NO cases (promises to report later, a question back, an
approval ask, a diagnosis with no fix). It makes model calls, so it is opt-in: `SESSION_OUTCOME_LIVE_JUDGE=1`. The
calls run in a child process, because under pytest core refuses to read the real auth store the pool needs.

Untested: core calling the provider and judge from its own refresh triggers (turn end, notice, card change).

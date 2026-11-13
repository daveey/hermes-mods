# session-outcome

Every non-archived chat should have exactly one stored outcome, and the sidebar paints from it rather than from
idleness. Core owns the storage and the derivation (`hermes_state_outcome`). This plugin lets the agent declare
the outcome and feeds core the two things only plugins know: open user decisions and a summary judge.

## Completion receipts

An optional `completion_receipt` on `kind: done` carries v1 scoped dispositions
(`delivered`, `superseded`, `contained`, `rejected`), evidence references with original
ISO8601 observation times and `reported`/`independently_checked` verification claims,
and remaining `pending`/`deferred` scope with qualified owners and reasons. This is
historical evidence, not execution authority or a replacement for workflow status.
Legacy declarations remain valid without a receipt and imply no artifact acceptance.
The loaded core's validator must preserve the field before the tool calls its writer;
an old running core that would discard it is refused, even if newer helper files exist
on disk. Non-done receipt arguments and malformed receipts fail before side effects.
Core still validates existing ownership for pending obligations at declaration time;
merely supplying a qualified reference cannot establish ownership. Deferred scope does
not authorize enforcement, publication or retries.

Tests: `tests/test_completion_receipt.py` covers schema, old loaded cores and non-done
admission; `tests/test_receipt_real_core.py` exercises the actual tool handler through
canonical storage/refresh, all four dispositions, deferral and atomic rejection. These
are isolated tests, not deployed UI proof. Wrapped tool/CLI/execute-code coverage is
not claimed by this plugin section.

## Behavior

- **Tool `session_outcome`.** It is offered only in desktop (including native Local chats whose stored source
  is `local`), tui, cli, gui and acp chats (a no-cache `check_fn`; the handler also refuses other session sources),
  and only when core has `hermes_state_outcome`. A bare `local` transport with no session row is not enough.
  Worker/off-switch and subagent exclusions take precedence over a stored interactive source.
  `tests/test_real_core.py::test_bound_local_transport_catalog_and_dispatch` exercises real stored sources,
  gateway ContextVars, registry catalog/dispatch, the gate and durable outcome readback;
  `test_native_local_exclusions` checks workers, the off-switch, subagents and unidentified local transport.
  These are isolated runtime integration tests, not live Desktop deployment proof.
  - `{"kind": "done", "summary": "..."}` declares completion. On a core with cron ownership lifecycle support,
    this releases ownership of live crons without stopping them or changing their schedules/delivery. The result
    names `released_crons`; `cron_release_error` reports partial failures. Add `"keep_crons": true` for a continuing
    watch whose chat should retain ownership. Older cores return an explicit unsupported-release note.
  - `{"kind": "decide", "items": [{"question", "choices": [≥2 {"label", "value"}], "card_id"?}]}` writes each
    question into the user-steps ledger with `add_step` (a `choose` step whose labels become its buttons), then
    declares `{"kind": "decision", "items": [{id, question, choices, state: "open", card_id?}]}`. Nothing is
    written when any item is malformed. Choices accept optional boolean `authorizes`: true marks a go
    button for the named action; false disables label inference. The selected label restricts scope.
    Hold/negative/conditional labels never authorize. "Only hermes" needs explicit true and an action
    question; without it, narrowing scope does not imply start-now. Omitted flags use user-steps'
    clear go/approve-label fallback. Tests: `test_decide_preserves_explicit_authorization_through_real_migration`
    and `test_decide_invalid_authorizes_writes_nothing` in `tests/test_session_outcome.py`.
    An item whose choices hold two or more go labels (user-steps `go_option()`: "Go now: X" / "Go now: Y") is
    refused and nothing is written: the run is settled and only a detail is open, so the hint tells the agent to
    pick the default, note it and how to undo it, and proceed. A real toss-up passes with the item's
    `why_not_default` (one sentence on a money, permission, other-person or can't-undo difference). Each refusal
    or override logs one `decide_go_variants action=refused|override` line (agent-f0722b8b79; tests
    `test_decide_*go*` in `tests/test_session_outcome.py`).
    After declaring, the result's `requests` lists each step's qualified `sourceRef`
    (`{gatewayId, profile, type: "user_step", id}`) and the canonical `requestId`/`revision` it created now
    (idempotent with the next turn's migration), plus an `upgrade` instruction: to ask approval of exact
    text for the same decision, cite that `sourceRef` in `action_request` `scope.sourceRefs`, which revises the
    request in place instead of adding a second tile. With no server identity an entry is
    `{stepId, identity: "unavailable"}`; no ref is fabricated. user-steps' `action_request` names any open
    declared decision of the chat a new publication does not cite (a hint; nothing merged by wording).
    Tests: `plugins/user-steps/tests/test_cross_producer_ask.py`.
  - `{"kind": "wait", "on": [{"kind": "card|process|cron|time|capacity|subagent", "id", "label"?, "state"?,
    "eta"?}]}` declares `{"kind": "waiting", "on": [...]}`. Each `card` found on this gateway's boards also gets a
    `tui` shepherd subscription for the chat (`delivery_metadata.via = "outcome-wait"`), so the chat's task list and
    sidebar show what it waits on; the result lists `linked` and, for cards it could not find, `not_linked` with the
    `kanban_origin_adopt` command. Accepted remote bindings in the current gateway/profile's origin ledger
    also count as `linked`; `link_notes` identifies the owner gateway. Another chat/profile/gateway's binding
    never counts, and an unreadable ledger reports unknown linkage rather than suggesting a new adoption.
    An owning (create/shepherd/origin) sub the chat already has is left as is.
    A card wait also returns `wakes_chat` (true when at least one card linked) and, for each card that did not
    link, a plain `warning` ("Nothing will wake this chat for <card>. Don't promise to watch it. ..."): `ok` and
    `declared` alone never mean a watch is armed. The coach logs it as `<- OK wait wakes_chat=...` for its judges.
- **Provider** `user-steps`: user-steps `open_steps(sid)` → `{"decision_items": [...], "waiting_on": []}`, or None
  when the chat has no open steps.
- **Judge**: is the last assistant message a closing summary? Every chat is judged, including a kind never seen
  before; only a background `source=` (core passes it) or session row (`kanban`, `cron`, `oneshot`, `subagent`,
  `tool`: `BACKGROUND`) gets None with no model call. An unjudged chat falls to core's length heuristic and shows
  green on any long reply, so an unlisted kind must fail toward a verdict (kanban-310ed068f0). `INTERACTIVE` and
  `BACKGROUND` must cover core's `kanban_session_provenance` lists; `tests/test_real_core.py` guards both. One call on the subscription pool
  (`pool-llm`'s `complete`, `anthropic`, `standard` tier, thinking off via `between_tools`, 300 tokens) with a 6 s
  budget. The judge writes one short sentence of reasoning and ends with a YES/NO line; only that last line is read.
  The bare one-token fast-tier answer called clear reports NO ("I posted the reply: <url>", a review ending in its
  verdict); the fast tier reasoning first, or as a majority of 3, still flipped borderline replies, while the
  standard tier got every live replay case 3/3 at 1.4-3.6 s. Thinking is off because adaptive thinking spent the
  whole token budget and calls came back empty. The judge sees the user's latest real request (`<request>`, capped at 2,000 characters; kanban
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
- **A held answer is never lost to a stub**: the nudge asks for the same full reply again ("the user has not seen
  it"; core also prefixes `[runtime] Your draft above was NOT shown…`). If the reply after this gate's own hold is
  under 40% of a held draft of 800+ characters, the gate returns `{"replace": <held draft>}`, logs
  `session-outcome: restored held draft for <sid> (N -> M chars)` and appends a row to
  `<home>/logs/held-draft-restores.jsonl` (the coach weekly's "Held-draft restores" KPI). Why: hub
  20261028_071229_45f849 held a full explanation; the model made the call and sent only "The explanation and
  recommendations are complete." Another gate's hold (nudge without `[session-outcome]`) is not restored.
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

`tests/test_real_core.py::test_done_cron_lifecycle_against_real_core` exercises the plugin handler with real
core storage for default release, explicit retention and corrupt-store error forwarding. The fake-core tests
also cover receipt passthrough and the older-core warning. Actual Desktop rendering is a separate rollout check.

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
- the judge: YES/NO, timeout → None, failure or unclear answer → None, only the last line is the verdict, the default path calls pool `standard` tier;
- the gate holds once when the judge gives up (error, unclear answer, cached unclear answer, timeout) and only the
  heuristic said done, and the second miss passes; the gate's judge budget is longer than a refresh's; the gate's
  scope does not leak into a later refresh; a wait that outranks the judge still passes;
- the judge gets a long reply's head and tail (whole when short), wrapped in `<message>`;
- the judge gets the user's latest real request (synthetic rows and the nudge skipped, capped), or none;
- the default pool path turns thinking off (`pool-llm` `complete(thinking=...)` puts it in the Anthropic body);
- the gate asks once and passes the second miss (by nudge tag and by the in-memory backup), passes after a tool
  call, on a summary, on an undecided judge, and when a wait or a card's ask outranks; an open user-steps step
  does not vouch for the reply (a NO reply is held, a YES passes), and outside the gate the step still counts;
- non-interactive sources (cron, slack, Kanban worker) get no tool and no gate; tui, cli, gui and acp do;
- a Desktop chat's turn admitted via the gateway `local` platform (no bound source) gets the tool from its
  session row, the same predicate the gate uses, so the gate never asks for a call the chat cannot make;
- the no-core no-op path.

User-steps' `open_steps`, `add_step` and the assent-echo filter are tested in
`plugins/user-steps/tests/test_outcome_helpers.py`.

`tests/test_real_core.py` requires the REAL `hermes_state_outcome` from `HERMES_OUTCOME_CORE_DIR`,
then `HERMES_CORE_CHECKOUT`, then `~/.hermes/hermes-agent`. It uses a temp HERMES_HOME state.db and sets
`HERMES_DISABLE_LAZY_INSTALLS=1` before importing core, so this library test cannot relaunch pytest into
production dependencies. Missing core or dependencies fail rather than skip. A live turn marker doesn't hide
the reply; a non-summary is held once; a judged summary passes; core's heuristic still returns done on judge
outage but the gate holds; and an open user-steps step makes core say `decision` while the gate still holds a
NO reply (including the regression assertion added with the provider-suppression fix).

```sh
scripts/test-all -k session-outcome
```

The runner prints named real-core results and skip reasons even on success. Both real-core tests must pass;
only the separate opt-in live model suite may skip. The runner's output retention is regression-tested in
`tests/test_test_all.py`. Use `HERMES_MODS_PYTHON` to select a dependency-equipped interpreter if the discovered
core `.venv` is not suitable; missing imports are errors, not a passing coverage gap.

`tests/test_judge_live.py` replays the real prompt on the real pool judge tier (3 runs per case, all must agree):
replies that were misjudged (the scheduled-cron answer, a posted-reply report, a review with its verdict) plus NO cases (promises to report later, a question back, an
approval ask, a diagnosis with no fix). It makes model calls, so it is opt-in: `SESSION_OUTCOME_LIVE_JUDGE=1`. The
calls run in a child process, because under pytest core refuses to read the real auth store the pool needs.

Untested: core calling the provider and judge from its own refresh triggers (turn end, notice, card change).

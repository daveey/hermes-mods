# Waiting-source inputs: change-proportional close-out reads

Design t_01a1d563324d7e8e §3 row C, condition C15 and review R11 (hub evidence:
`~/.hermes/kanban/evidence/t01a1d563-change-proportional/`). Code: `waiting_inputs.py`, `waiting_source`,
`waiting_inputs` and `wake_tick` in `__init__.py`, `core.pending_closeouts(rows=, row_key=, cache=, stats=)`.

## Why

Before this change, the sidebar waiting source was registered without `inputs=`
(`__init__.py:1657-1664` at 6c5e4da6; the card cites `1606-1612`, which had drifted — C18). Without `inputs=`, core's
annotation host could not vouch for it, so every annotation adoption re-ran `pending_closeouts`. Each run did a full
reconcile and transcript proof for every pending row (`transcript_key` was 61 of 131 in-holder samples). The
measured cost was 15.3% of the busiest serve's samples, the gateway host pass was about 3.0% of a core, and the serve
loop about 0.8%.

## The inputs

`waiting_inputs()` is registered as `register_waiting_source("chat-closeout", waiting_source, inputs=waiting_inputs)`.
It returns plain JSON that moves whenever `waiting_source` could answer differently. If an input can't be read, it
raises, so no fingerprint is produced and core reads locally. It is made of:

| part | what | how it is read |
|---|---|---|
| `rows` | each pending ledger row's settlement fields (`core.SETTLE_ROW_FIELDS`; the waiting pass settles from only these, so the waker's bookkeeping writes — claims, retries, wake results — are no input) | re-read only when the ledger's committed revision moves (`core.revision_token`) |
| `origin` | digest of the origin ledger's obligations and receipts | re-read only when its committed revision moves |
| `boards` | certified per-task revision marks for pending batch tails, plus the FULL mark and file/schema identity | indexed marks in the same transaction as core's trigger/counter/identity certification; the global counter certifies but is not a key value |
| `shadows` | selected task presence and `SHADOW_FIELDS` for every discoverable shadow plus freshness judged at now | `core.shadow_rows` selected-content snapshots (invalidated by committed `data_version`); no task-change cursor requirement for legacy shadows; known absent shadows stay explicit |
| `replica` | substantive digests of the hub replica `tasks` rows for the batch cards, the owners' scope heartbeats and the sync meta; every clock verdict is judged at now | `ReplicaWatch`, only when the replica's revision moved: a per-gateway index-only probe (`count`, `count(seq)`, `max(seq)`, `total(seq)`), the rows past each gateway's cursor, and every tracked batch row re-read by primary key (raw text compared, so a rewrite in place at its old seq moves it). A NULL `seq`, a probe the merged rows don't reproduce (a delete, a rewrite at an older seq, a new gateway without an info row), a replaced file (dev, ino) or new batch tails rebuild |
| `lineage` | per-lineage transcript watermark for each pending chat and its compression tip: max `messages.id` (any row), the active rows' max id, count, id sum and timestamp sum (core `transcript_key`'s terms: an active flip or delete below the max, a timestamp repair), `transcript_revision`, `end_reason`, compression-child count | `LineageWatch`: index-only seeks (`idx_messages_session_active`, `idx_messages_session_id`), re-run only when that state.db's stat moved. A db whose triggers can't vouch that `transcript_revision` moves on an in-place rewrite (core `TRANSCRIPT_TRIGGERS`) has no watermark: no key, a full pass every time, like `transcript_key` returning None |
| `floor` | a 10-minute bucket, in the whole key and in every per-row key | a missed input is recomputed within 10 minutes, per row too |

R11: the global state.db stat is never part of a key value, because it moves in about 47% of 2 s windows. It only
gates re-running the per-lineage seeks in-process. A message written in a chat with no ledger row leaves the key
unchanged (`test_a_chat_outside_the_ledger_does_not_move_the_key`).

The replica's liveness-only fields are left out of the digests: `activity_at`, `activity_kind`, and the
`origin_observation.task_hash` re-stamp they cause. These fields are 67% of changed task rows in rf2. Whether the
observation still verifies (`_owner_evidence`) is kept, so a real attestation change still moves the key.

### Irrelevant write storms

Problem: the declaration existed but its whole-board values moved on unrelated comments and task writes,
forcing waiting reads on almost every holder wake. Root (happened): a global invalidation domain despite
the known batch-tail selection. Root (not caught): tests checked per-row work, not unchanged declarations
and zero source calls during irrelevant write storms. Recurrence: this extends the earlier host-only
scoping fix (`_host_board_key`); that history-dependent token cannot certify portable waiting inputs.

`_waiting_board_key` instead publishes deterministic selected task marks. A cold observer computes the
same value independently; its cursor is used only to reject FULL/restore/regression, never as a value.
Missing certification, unknown freshness, errors and older cores force fresh reads. Failed declarations
also discard the same-thread reusable input and bypass the three-second answer and row memos.

Proof: `tests/test_waiting_task_inputs.py` covers unrelated heartbeat/comment/status writes, cold-key parity,
selected changes without notifications, batch growth, floor rollover, missing coverage/errors, FULL/reinstall,
same-file counter regression and replacement. Its composed holder test runs the actual core declaration gate
against twenty real state-message/comment/heartbeat storms with zero extra source calls, then selected-change
and clock controls. On a core predating the gate only that composition skips; use `HERMES_WAITING_GATE_SOURCE`
to exercise an exact candidate module without changing the installed core. Live CPU acceptance remains a
separate integration measurement, not established by these tests.

## Per-row reuse

`pending_closeouts(..., row_key=, cache=)` keeps one cached answer per row, keyed by
`row_key(row, cards as read, lineage watermark, floor bucket)`. A pass after an input moved re-reconciles only the rows
whose own key moved:
- a batch card changing re-reconciles that row;
- an ack landing in a transcript re-proves that chat;
- a ledger write re-reconciles that row.

The batch read itself (one union query per pass) still runs, because a row's key includes its cards as read.

`waiting_inputs()` leaves its reads for the waiting pass core runs right after a fingerprint it could not adopt
(`INPUTS_REUSE_S` = 1 s), on the same thread only. Those reads were taken before the pass's own, so a change in
between only re-runs the next pass. When those reads differ from the inputs of the 3 s answer cache, the pass skips
that cache: core never publishes an older answer under the newer key.

## Host pass

`wake_tick` (the host pass's wake step) computes host inputs (whole rows) inside its proof pass.
Unlike the portable waiting-source key, host board/shadow keys use core's coverage-checked
`kanban_task_change.changed_since` cursor to ignore changes positively attributed to tasks outside the pending
batch tails. Batch-tail growth, a relevant task/link change, a file/schema replacement, revision regression,
unknown/full changes, missing coverage or an older core all refresh the broad key. Shadow freshness is still
judged on every tick. This memo is process-local and distinct from portable selected-task evidence. Owner-wake retains the
**global** board/shadow keys because parked cards need not occur in any batch. Wake and watchdog also key the
unseen-notice map (subscriptions may name non-batch tasks), and wake's row cache keys it too: clearing unseen
notices must reconsider an idle row immediately. These are input probes, not repeated verdict/proof work.
- A row that was **quiet** under exactly its current key is not judged again. Quiet means: no close-out key, no ask
  key, no unlinked entry, no missing or stranded batch entry, nothing written.
- A row with a close-out or ask key that was judged **not due** and wrote nothing is *idle* under its key until its
  own clock can change the answer: the end of the chat's 5-minute typing quiet period or the oldest wake leaving the
  hourly cap window (`_idle_until`). It is not read, queried or judged before then; a change to its row, cards or
  transcript moves the key and re-judges it at once. A row whose claim was attempted, or that wrote, is judged again.
- A row whose only activity is the unlink observation clock of its missing or stranded entries (`track_unlinked`
  writing only `unlinked`) is a *clock row*. Its clock fields (`at`, `mono`, `pid`, `clocks`, `observed`) are left
  out of the host key (`_host_row`), and while the rest of the inputs are unchanged the tick only replays
  `track_unlinked` on the cards as last read and writes the clock (`_clock_tick`): the write carries accrued healthy
  time across gaps and processes, so it is kept. No board or state.db query, no judgment, no proof. An entry that
  reaches its removal, or any other field in the result, runs the full pass instead. Accounting: the tick counts as
  a `chat-closeout.wake_tick` skip and as a run of `chat-closeout.wake_tick_clock` (rows = clock rows, changes =
  ticks that wrote).
- When every row was quiet, idle or a clock row and the whole input set is unchanged (the floor bucket included),
  the next pass returns at once or does only the clock writes. The memo also ends at the earliest idle deadline.
- `owner_wake_tick` skips while the board, shadow, replica and origin keys are unchanged and no parked card is due
  (it ends when a waiting agent-owned card would turn stalled). `watchdog_tick` skips while its last full pass
  observed nothing, the whole key is unchanged, the unlink clocks it held out of "stuck" still advance, and the
  ledger holds no flag or stalled handoff. Both reuse the wake step's keys of the same tick (at most 20 s old) and are
  counted as skips of their own passes.
- Injected readers, proofs, clocks, or `only=` (residual passes) bypass the keys.

## Accounting (C15)

Every pass reports through core's P1 hook `hermes_cli.loop_accounting.pass_` (card A). On older cores the hook is a
no-op.
- `chat-closeout.waiting`:
  - `skips` = passes answered from the unchanged inputs;
  - `rows` = rows re-reconciled;
  - `fallbacks` = passes where an input was unreadable and a full pass ran instead.
- `chat-closeout.waiting.changes` counts input-key movement, even when the answer stays identical.
- `chat-closeout.waiting_moved`: conditional reconcile CPU and rows on input-moved passes only; `cpu_s / runs`
  measures triggering-pass reconcile cost for C15. Cold initialization counts as moved.
- `chat-closeout.waiting_fallback`: conditional reconcile cost when no trustworthy input key was available.
- `chat-closeout.waiting_inputs`: the key computation core runs on each annotation fingerprint. An input with no
  portable key (`Unportable`, e.g. a zero-budget `BoardLifecycleBusy` board read) is the designed no-adoption
  signal: it counts as a `fallback`, never an `error`, and is re-raised outside the pass so core still skips
  adoption and runs `waiting_source`. Only a real failure (`sqlite3.OperationalError`, an unreadable state.db...)
  counts in `errors`; cores that keep `error_kinds` name its class there.
- `chat-closeout.wake_tick`, with the same fields, plus `owner_wake_tick`, `owner_relay_tick` and `watchdog_tick`.

Card B budgets triggering-pass cost using `waiting_moved` and charges key probes separately. The moved/fallback
spans are **subsets** of `waiting`, not additional CPU: do not add their totals to `waiting.cpu_s`. Source-local
key probes are included in `waiting`; declared fingerprint probes are in `waiting_inputs`. The total waiting
budget is `waiting + waiting_inputs`, not the sum of all four spans. Read the counters back from
`~/.hermes/run/loop-accounting/<pid>.json`.

## Benchmark

`bench/replay_bench.py` builds a home at hub scale and replays the measured chg1/rf2 change rates (600 s in 2 s
windows), then reports each loop's CPU as a percentage of one core. Wall/monotonic clocks advance coherently
for all production readers, including floor and freshness deadlines; CPU timers remain real. Budget overruns
exit 1 (use `--diagnostic` only for explicitly non-acceptance comparisons). See the PR for before/after numbers.

## Watchdog section accounting

`watchdog_tick` runs its parts inside permanent, behavior-neutral accounting scopes, so a live
`~/.hermes/run/loop-accounting/<pid>.json` read attributes its CPU: `chat-closeout.watchdog.<section>` with sections
`inputs` (key build and skip check), `reader`, `unseen_counts`, `ledger_reads`, `unseen`, `closeout`, `ask`,
`stuck_cards`, `stuck_ledger`, `handoffs`, `flags`, and `session_gone` (the deleted-chat probes, nested in the loop
sections). Their CPU is also inside `chat-closeout.watchdog_tick`; sections nest, so do not add `session_gone` to its
parent. No threshold, clock, TTL or outcome depends on them.

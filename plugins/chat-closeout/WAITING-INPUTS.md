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
| `boards` | each local board's content key (`ksa._board_content_key`: bookkeeping writes excluded) | core's cached key |
| `shadows` | each sync shadow's content key plus its freshness judged at now | core's cached key, `core.shadow_rows` cache |
| `replica` | substantive digests of the hub replica `tasks` rows for the batch cards, the owners' scope heartbeats and the sync meta; every clock verdict is judged at now | `ReplicaWatch`, only when the replica's revision moved: a per-gateway index-only probe (`count`, `count(seq)`, `max(seq)`, `total(seq)`), the rows past each gateway's cursor, and every tracked batch row re-read by primary key (raw text compared, so a rewrite in place at its old seq moves it). A NULL `seq`, a probe the merged rows don't reproduce (a delete, a rewrite at an older seq, a new gateway without an info row), a replaced file (dev, ino) or new batch tails rebuild |
| `lineage` | per-lineage transcript watermark for each pending chat and its compression tip: max `messages.id` (any row), the active rows' max id, count, id sum and timestamp sum (core `transcript_key`'s terms: an active flip or delete below the max, a timestamp repair), `transcript_revision`, `end_reason`, compression-child count | `LineageWatch`: index-only seeks (`idx_messages_session_active`, `idx_messages_session_id`), re-run only when that state.db's stat moved. A db whose triggers can't vouch that `transcript_revision` moves on an in-place rewrite (core `TRANSCRIPT_TRIGGERS`) has no watermark: no key, a full pass every time, like `transcript_key` returning None |
| `floor` | a 10-minute bucket, in the whole key and in every per-row key | a missed input is recomputed within 10 minutes, per row too |

R11: the global state.db stat is never part of a key value, because it moves in about 47% of 2 s windows. It only
gates re-running the per-lineage seeks in-process. A message written in a chat with no ledger row leaves the key
unchanged (`test_a_chat_outside_the_ledger_does_not_move_the_key`).

The replica's liveness-only fields are left out of the digests: `activity_at`, `activity_kind`, and the
`origin_observation.task_hash` re-stamp they cause. These fields are 67% of changed task rows in rf2. Whether the
observation still verifies (`_owner_evidence`) is kept, so a real attestation change still moves the key.

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

`wake_tick` (the host pass's wake step) computes the same inputs (whole rows) inside its proof pass.
- A row that was **quiet** under exactly its current key is not judged again. Quiet means: no close-out key, no ask
  key, no unlinked entry, no missing or stranded batch entry, nothing written.
- When every row was quiet and the whole input set is unchanged, the next pass returns at once.
- A row that acts (has a close-out or ask key, or wrote the ledger) is judged on every pass, because its quiet period,
  wake cap and claims are clock-driven.
- Injected readers, proofs, clocks, or `only=` (residual passes) bypass the keys.

## Accounting (C15)

Every pass reports through core's P1 hook `hermes_cli.loop_accounting.pass_` (card A). On older cores the hook is a
no-op.
- `chat-closeout.waiting`:
  - `skips` = passes answered from the unchanged inputs;
  - `rows` = rows re-reconciled;
  - `fallbacks` = passes where an input was unreadable and a full pass ran instead.
- `chat-closeout.waiting_inputs`: the key computation core runs on each annotation fingerprint.
- `chat-closeout.wake_tick`, with the same fields, plus `owner_wake_tick`, `owner_relay_tick` and `watchdog_tick`.

Card B budgets on these numbers. Read them back from `~/.hermes/run/loop-accounting/<pid>.json`.

## Benchmark

`bench/replay_bench.py` builds a home at hub scale and replays the measured chg1/rf2 change rates (600 s in 2 s
windows), then reports each loop's CPU as a percentage of one core. See the PR for before/after numbers.

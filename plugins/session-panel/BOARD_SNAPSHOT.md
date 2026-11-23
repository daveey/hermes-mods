# Unactivated board-snapshot/1 API

This acquisition/store milestone is **not the active feed implementation**. No Feed,
BoardLane, timer, default-off switch or Desktop consumer imports it. Import starts
no thread and retains no connection. Existing production ownership, concurrency,
reconciliation and publication are unchanged. Consumers, reserved scheduling and
receipt instrumentation belong to the next milestone; no live CPU/freshness claim
is made by this capability.

## Immutable store

`dashboard/board_snapshot.py` exposes frozen SourceKey, CardKey, ChatKey, Watch,
SourceVersion, ScopeId, ScopeDemand, Coverage, Receipt, Membership, SourcePart,
Snapshot and Admission values. FrozenMap owns its recursively frozen backing data;
mutable nested dataclasses and borrowed mapping proxies are refused. `thaw` gives
mutating presentation helpers their own copy. Identity includes gateway/profile/
board, never just a card ID.

Use one `SnapshotStore` per serving profile/runtime epoch. `add_source` registers
canonical coordinates. `register` allocates a never-reused watch token and advances
lineage generation; `require(chat, refs)` advances refs generation. Freeze exact
`demands(source)` under its lock, then release the lock for acquisition I/O.

`accept_local(part, prior_version)` and `accept_external(...)` CAS the source epoch,
generation, revision and owner token. Only matching current completed ScopeDemands
gain coverage. New/expanded/rewatched scopes remain missing at unchanged DB stamps.
Carried scopes retain their original scope receipt/version; changed shared detail
or edge payload revokes incompatible old coverage. Membership and tombstones apply
only to certified scopes. Old tokens cannot recreate membership. Old/new union
membership and dependency keys drive invalidation. SourcePart.invalidated_keys
carries cursor-observed changes even outside the captured membership; it revokes
carried certificates without deleting last-known payload. Reuse requires current
capture or same-cursor scope provenance, not mere presence in a merged part.

`pin(chat)` returns Snapshot plus Admission, preserving first unserved clocks,
receipt IDs, required scopes, certified source versions, missing scopes, failure
vector and missing-source versions. The latter fence even an already-unknown
projection after source reset. `current(admission)` checks the exact vector without
I/O. Call `acknowledge(admission)` only after the consumer's successful exact-vector
publication; acknowledgement is not itself publication or an emission receipt.

`fail(source, generation, error, affected, prior_version=...)` retains last-known
payload and marks specified current scopes unknown. Pass captured prior_version to
reject late failures after newer same-generation success. Success clears only its
completed current scopes. `reset` rotates owner/generation and revokes coverage.
`forget` removes scoped payload, pending custody and reverse membership; rewatch
must reacquire. `close(epoch)` fences the owner. `transfer` accepts a compatible
stopped in-process store once, preserves immutable versions and first admission
ages, and rotates capture owner tokens. `export_resume` is an observation, not disk
restore authority; no trusted disk-import API exists.

Default capacities: 400 watches, 64 sources, 65,536 lifetime tokens, 4,096 pending
receipt IDs per chat and 4,096 refs per chat. Exceeding them raises SnapshotCapacity,
never silent truncation or false completeness.

## Canonical acquisition

`CoreReaders.snapshot_modules()` lazily loads a content-qualified package. Use its
`snapshot` types consistently. `capture_local(source, prior_part, frozen_demands,
CaptureHints(...))` delegates to board_capture.py. Hints include prior store version,
owner token, pinned observation time, 0 < deadline <= 2 seconds and SQL variable
limit 32..999.

Resolve canonical card_scope(path) before one owned query-only transaction. It
reads task-change cursor, complete JSON/json_each union creator/subscriber pairs,
canonical execution/wait enrichment, remote-parent projection and bounded union
closure. Core acquisition policy is reused, not copied. Missing files, SQL failure
and deadline overrun raise rather than certify empty. No reader DDL/migration is
performed. Connections close before returning or pure projection. Source-generation
reset forces rebuild even at an unchanged cursor.

Each dirty detail is hydrated once across all chat closures with existing depth/
card bounds. One JSON-bound recursive dependency query covers enrichment ancestors
past display hydration stops; prior reverse edges also invalidate explicit producer
refs and embedded parent evidence. Any remote-parent projection change conservatively invalidates
the retained local union, including parents never hydrated. That canonical table
has no owner column: raw rows use `(SourceKey, parent_id)`, not an invented remote
CardKey. Its complete projection is retained in the receipt and remote_parents.

Unchanged detached rows/details are reused without renewing execution observations.
The future source scheduler must refresh at canonical evidence expiry boundaries.
Existing exact bounded event tail and latest-run semantics are retained. Core
execution enrichment can still scan history for its authority policy; VM costs
include that work. Bounded returned rows do not establish bounded total SQL work.

`capture_external(frozen_demands, refs, prior_parts, hints=...)` reads existing
strict canonical union inventory once. One aggregate external SourceKey is a
conservative atomic failure domain, independent of local success. The optional
include_inventory result and snapshot_select_external reuse canonical origin/
release/replica/history rules, including unfinished pending release selection.
Existing external_capture callers retain the old return shape. Remote closure
uses short-lived query-only shadow snapshots and validates owner before hydration;
these are separate observations, not one atomic strict-inventory transaction.
Unavailable details mark scopes unknown. The shared shadow has no profile-specific
board authority, so non-default-profile detail requests remain unknown rather than
borrowing another profile's card. Qualified refs owned by the serving profile's
configured local boards stay with local capture, never enter remote shadow
hydration, and do not make otherwise healthy external coverage unknown. Unresolved
remote refs still refuse coverage. Deadlines apply even to empty results.

## Pure readers and consumer boundary

`SnapshotReaders(snapshot, chat)` provides lineage, linked, done, card and fully
qualified card_key, with no panel_inputs or decision-store API. It performs no
SQL/network I/O and returns detached copies. Creator precedence and terminal
history remain per-lineage. Legacy `(id, board)` succeeds only for one scoped
owner/producer; collisions produce a diagnostic and missing detail.

Missing/failed coverage raises CoverageUnavailable. A consumer must retain stale/
unknown presentation and custody, never catch it as authoritative empty or mark
heartbeat-fresh. Partial local publication during external outage requires the
follow-on projector's explicit unknown-source merge; this strict reader alone does
not implement that merge.

Still outside this milestone: reserved-role scheduling, real local publication
during held external capture, evidence-expiry scheduling, observation-only pull
carriage, all consumer cutovers, authenticated emit/catch-up receipts, serialized
writer contention, native Desktop proof and live process CPU/freshness acceptance.
Do not activate duplicate shadow producers. Rollback is revert/removal of this
unused capability, leaving production decision and board owners unchanged.

## Tests and costs

Run canonical scripts/test-all with an isolated compatible core, plus repo-guards.
The test_snapshot_{store,custody,acquisition,external,scope_capture,cost} suites
exercise real SQLite/core differentials at pinned time, 1/8/64 chats, 501/4096
lineage IDs under 999 variables, shared-detail reuse, scope races at unchanged
stamps, stale tokens/failures, reset, remote parents, bounded history and pure
no-I/O projection. Real Feed reducer comparisons include sections, header, action
targets and revision. No acquired board state authorizes a decision.

`tests/bench_board_snapshot.py --chats N --shared-cards N --history-events N
--seconds N --output PATH` uses disposable state and the canonical core interpreter/
test-site dependencies on PYTHONPATH. It reports acquisition SQL/VM/detail counts,
pure reducer cost, CPU and memory. Publication receipts remain zero and missing
receipts explicit; it cannot pass end-to-end/live acceptance. Required acquisition
fixtures include 64 chats/40 shared cards/5000 events and one 4096-ID lineage under
999 SQL variables. Preserve raw reports/query plans and source SHAs privately.

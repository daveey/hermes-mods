# Canonical listed-card acquisition on caller-owned snapshots

Source patch ordinals: 5834–5836.

## Behavior

`read_session_task_set` returns enriched canonical listed cards plus complete `(session, task) -> via` membership on caller-owned SQLite readers. `enrich_session_tasks` reuses the canonical execution-authority, details, receipts and wait policy. Neither helper owns a connection, transaction, migration, persistent cache or store activation.

## How it works

Resolve `scope` with canonical `card_scope(path)` **before** entering the capture transaction: cold identity discovery can otherwise open an unrelated identity database. Then supply the query-only transaction, `sqlite3.Row` row factory, explicit `observed_at` and resolved scope. `path` supplies scope metadata; this helper never opens it.

`select_listed_local_task_set` binds JSON session/status sets through `json_each` using two variables independent of lineage size. Creator precedence applies both to each membership pair and to the legacy union; a union-level `via` must not overwrite a different chat's subscription provenance. The strict selector propagates subscription SQL errors when the table exists, rather than presenting partial membership as complete. Missing legacy subscriptions and pre-session schemas remain explicit supported cases; only the historical union wrapper uses `strict=False`.

`replica=None` is **local-only**, not a request to discover a shadow. An explicit replica reader is borrowed, never opened or closed by acquisition. `LEGACY_REPLICA` is the legacy ellipsis sentinel and retains legacy shadow discovery/closure and helper call shape. Legacy waits use the current wall clock; owned captures use `observed_at`. Foreign/multihop parent evidence must be captured and supplied by the caller.

Detail batches use `min(500, variable_limit - len(_KINDS))` for fixed bindings; execution joins use `min(500, variable_limit // 2)` because each chunk is bound twice. Containment ID scopes use JSON bindings. Legacy detail enrichment acquires at most one discovered replica for all chunks and closes only that owned reader.

## Code

- `hermes_cli/kanban_session_acquisition.py`
- `hermes_cli/kanban_session_outcome_tasks.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_activity_card_details.py`
- `hermes_cli/kanban_execution_source.py`
- `hermes_cli/kanban_session_wait_rows.py`
- `hermes_cli/kanban_contained_wait.py`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`tests/hermes_cli/test_kanban_owned_acquisition.py` covers complete membership at large sizes under SQLite's 999-variable limit, cursor/rows sharing one physical snapshot, no implicit opens, explicit-replica custody, local-only containment, optional details and doubled-binding chunks. `test_kanban_acquisition_boundaries.py` covers strict subscription errors, scope pre-resolution, one legacy replica across chunks, watch/counting policy and explicit legacy schema cases.

`test_kanban_acquisition_differential.py` compares authority, receipts, current-run and primary-owner evidence, waits, optional failures, named-board scope and change-cursor snapshot behavior. `test_kanban_acquisition_parents.py` covers supplied foreign multihop evidence and no implicit external acquisition. `test_kanban_acquisition_legacy_custody.py` pins legacy discovery/fallback, unchanged optional-helper calls, borrowed readers remaining open, absent-reader lookup controls and wall-clock versus frozen-observation policy.

## Applying

The canonical source feature lands in these three ordered commits:

- `e75617ea199ad84b2e290548fe7d2f915854edcd` — acquisition and shared enrichment.
- `17ab08071d03638060f39476efcfeb2a93037b88` — legacy fallback and borrowed-reader custody tests.
- `33774b3fc6826f1bd028a60b5c31963caa99da8c` — legacy clock and absent-reader controls.

Use the full ordered mirror dependency chain. A caller resolves scope before capture, opens its own read-only/query-only transaction(s), establishes snapshots, invokes acquisition, then closes its own readers. No DDL, migration or new store activation is introduced.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

SQL chunks respect actual parameter arity; that is not a bound on membership, lineage size, execution-history/window scans or presentation history. Those remain canonical and may be large. Two caller-supplied readers are not automatically an atomic cross-database snapshot. Membership is full before union creator precedence, and consumers must apply per-chat provenance themselves. Passing `None` and omitting a legacy replica argument intentionally have different semantics.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.

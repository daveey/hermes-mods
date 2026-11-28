# Native owner release: journaled HOME application (dormant until Board support)

Patch coverage: 6758, 6759, 6760, 6761, 6762, 6763, 6764, 6765, 6766, 6767, 6768, 6769, 6770, 6771, 6772, 6773, 6774, 6775, 6776, 6777, 6778, 6779, 6780, 6781, 6782, 6783, 6784, 6785, 6786.

## Behavior

A staged native path lets the *original owner* release a held card and have the release applied at the card's HOME board, with durable custody at each step. The first commits are explicitly producer-only: they fail closed until the Board's `owner_release` v1 capability exists, advertise no capability, and make no live root action. The later commits assemble and prove the local composition, but this document makes no deployment claim.

Layers, in order:
1. **Intent journal** (`kanban_release_intent.py`): frozen canonical intent, stage CAS and exact-expiry one-successor acknowledgement; the CLI binds to frozen intents and reconciles a frozen invocation before mutable preflight; complete release inventory emission is confirmed before it is acknowledged; the exact carrier is journaled with separate intent/carrier/row-operation identities.
2. **Private consumption**: authenticated, bounded private certificate parsing and Hub GET, cached host-locally and immutably; the consume wire is journaled before HTTP.
3. **HOME application sidecar**: an immutable receipt of the consumed certificate, delivered wire, renewal, result and selected release effects; native readers (`kanban_release_read.py`) require **APPLIED** proof; legacy comment mandates and cumulative accounting stay intact.
4. **Atomic local effects**: budget and promotion compose in one writer (rolled back as a unit on any later refusal); rollback-only proposals keep prepared evidence but never placement/APPLIED authority; certified materialization is atomic and imports only events/comments; results are reconciled and certified proof is transferred to foreign executors.
5. **Accounting**: ordinary (ungoverned) cards keep ordinary accounting through an authenticated release; strict v1/v2 versions; Board-attested v2 governance accepted by the readers.
6. **Containment**: marker erasure cannot downgrade retained private identities to legacy comment authority (hold-only); refused HOME budgets are isolated per delivery (typed refusal only) without clearing custody; limits on source bytes, list and row counts.
7. CLI ingress: new `kanban release --episode <exact episode>` and `--fresh-after-expiry <expired intent uid>` options. The native route is taken only when `--episode` is given, from the live owning-chat native terminal bridge (no standalone fallback), for an exact single card whose HOME is a foreign gateway; it first reads the Board's `owner_release` capability (exact integer 1) and otherwise returns `board_unsupported` with nothing sent. Without `--episode` the legacy local release is unchanged (and `--fresh-after-expiry` alone is refused). The origin CLI gate additionally records `expired`/`refused` terminal stages as definitive not-ok results.

Activation boundary: the code is not disconnected from ordinary paths. The HOME queue `tick` runs from `kanban_operator_call.apply_incoming`, `kanban_sync_quarantine.certified_page` runs executor certification, and `kanban_worker_node.import_cards` adopts executor receipts on every pass; these are inert unless the queue/receipt tables or Board-emitted release carriers exist, i.e. dormancy rests on the Board not emitting carriers/capability, not on the wiring being absent. Quarantine kinds `owner_release_conflict`/`owner_release_oversize` are added to the no-generic-release list. Later commit text that says the production queue is "disconnected" (6768/6769) describes the state of those commits, not the final series.

## How it works

Main modules: `kanban_release_{intent,native,certificate,delivery,application,assemble,prepared,final,final_home,materialize,result,executor,home,home_queue,public,read,replica}.py`, `kanban_cli_native.py`, `kanban_origin_cli_gate.py`, `tools/origin_cli_{bridge,policy,reply}.py`, `kanban_db_promote.py`, `kanban_spend_budget.py`, `kanban_scope_renewal.py`, `kanban_sync_hub_routes.py`, `kanban_sync_quarantine.py`.

## Code

All the files named above; fixtures `owner_release_application.json`, `owner_release_executor_board.json`, `board_emitted_owner_release_consumption.json` and a test runner script.

## Tests

Read from source; **not executed here**. `test_native_owner_release_cli.py`, `test_origin_cli_release_gate.py`, `test_owner_release_accounting_v2.py`, plus owner-release readers/final/materialize/quarantine suites (the commit texts quote 137-case style counts as owner reports). Stacked controls run through a *test* Board transport; behavior against a deployed Board is **untested** here. Authenticated HOME release of a real card was not exercised in this mirror.

## Applying

Apply the whole range as one dependency unit in ordinal order; later commits repair earlier ones (6761 reorders reconcile before preflight; 6767 tightens readers).

## Gotchas

- Dormant until the Board serves the matching capability; do not enable without it.
- Never trust a public marker alone; APPLIED proof or hold-only classification governs.

## Why core, not a plugin?

Core Kanban release/accounting machinery.

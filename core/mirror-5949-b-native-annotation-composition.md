# Scoped native annotation capture, composition and rollback fencing

Mirror ordinals: **5941, 5942, 5943**.

## Behavior

These ordinals prepare native fact-backed annotations for seven families: Kanban, background processes, turns, subagents, review waits, goals and cron membership. Request-side annotation calls can consume immutable, exact-profile views instead of performing their legacy reads. Missing, stale or unauthorized views are unavailable, not fresh empty success; cold batches demand every missing selector without partially mutating rows.

**This range is an inert implementation checkpoint:** it adds composition and bootstrap helpers but does not wire web lifespan activation or turn the feature on by default. Later activation changes are outside this document's owned ordinals. No live performance or deployment claim follows from this preparation.

## How it works

### Detached inputs and pure views — 5941

`NativeAnnotationSource` runs operational captures through the existing ChangeFeed. It freezes and encodes input, enforces capture limits, records source identity and monotonic scheduling, and publishes `AnnotationInput` facts to canonical reducers. Projections reuse existing serializers rather than creating alternative work-state rules. Clock boundaries can reduce an unchanged input again; native hints do not postpone an outstanding boundary.

Turn captures read live leases and pause evidence, publishing only a one-way holder digest rather than the raw compare-and-swap credential. Background captures combine the authorized process checkpoint with live registry evidence and profile/liveness checks. Child capture reads eligible subagent rows. Review capture selects latest assistant evidence per demanded lineage and reuses bounded asynchronous provider lookup; its aggregate assistant-byte limit precedes provider lookup. Missing lineage/provider evidence remains unavailable rather than a fresh empty review result.

Review demand validates exact selectors and inventory bounds and admits the mailbox before changing capture membership. Rejected demand therefore cannot poison already admitted views. `AnnotationRegistry` holds authorized bindings only: exact named-profile lookup never discovers a missing profile. Mixed-profile annotation works on detached row copies and publishes mutations only after every group succeeds.

### Executable composition and lifecycle hints — 5942

`AnnotationRuntime` builds one shared store/feed/transport partitioned by explicit authorized profile homes. Source contexts are bound to those homes without hydrating secrets. It seeds canonical output selectors, waits for fresh inputs and seeded outputs, then transitions bindings to native mode and installs lookup. Failed readiness retains legacy custody and never installs the native lookup.

Detached Kanban capture reuses canonical board transactions, execution observations, local/remote identity rules and external-wait projection. Independent board reads form source evidence, not a fictitious cross-database transaction. Strict native external reads propagate failures; the legacy memoized reader retains its historical forgiving behavior. Cross-board attention counting preserves canonical identity deduplication.

Content-free process-registry hints wake the background source before optional persistence, including registry-only jobs. The hint carries no command, PID, profile identity or authority; the owner subsequently captures actual state. Subscriptions are registry-specific and disposable, and a failing listener cannot prevent peer wakes or process completion.

### Global rollback fence and bootstrap discovery — 5943

A serialized set of native profiles keeps process-wide legacy producers stopped until the **last** profile returns to legacy. Failed stop custody is retained for rollback. Runtime close is idempotent after successful cleanup.

`fact_annotation_boot.seeds` unwraps canonical route decorators during authorized bootstrap, intercepts annotation calls to collect only canonical selector identities, preserves ordered lineage and discards route bodies. It does not populate a second response cache or run legacy annotations. Collector context is reset even on failure. `prepare` composes the authorized roster, seeds it and closes the runtime on failed start, but is not called from web lifespan in this owned range.

## Code

All paths refer to the Hermes source tree:

- `hermes_cli/fact_annotation_capture.py`: bounded read-only state/file/registry capture and holder-digest nonpublication boundary.
- `hermes_cli/fact_annotation_sources.py`, `fact_annotation_views.py`: shared-feed adapters, demand admission and canonical projections.
- `hermes_cli/fact_annotation_startup.py`: exact-profile registry and memory-only lookup.
- `hermes_cli/fact_annotation_kanban.py`, `kanban_external_activity.py`: detached Kanban and strict uncached external reads.
- `hermes_cli/fact_annotation_runtime.py`, `fact_annotation_boot.py`: scoped composition, readiness, global rollback custody and bootstrap-only seeds.
- `hermes_cli/kanban_annotation_facts.py`: lookup bridge, batch atomicity, demand-all and seed interception.
- `hermes_cli/review_session_activity.py`, `turn_session_activity.py`, `turn_process_execution_observations.py`: provider refresh wakes and reusable pure/digest projections.
- `tools/process_registry_events.py`, `tools/process_registry_checkpoint.py`: content-free registry lifecycle publication.

## Tests

**Source coverage only; none were executed in this mirror.** All listed files are under `tests/hermes_cli/` in the source tree.

| Test file | Covered contract |
|---|---|
| `test_annotation_capture_bounds.py` | Review demand cap, failed mailbox admission without membership poisoning, aggregate assistant-text bytes checked before provider lookup |
| `test_annotation_capture_privacy.py` | Raw lease-holder credential absent from encoded facts and views; digest projection preserves canonical output under request-I/O tripwires |
| `test_annotation_native_families.py` | Decorated Kanban/background/turn/subagent/review entry calls through real selectors/reducers, canonical parity, unknown review evidence and quiet clock boundary |
| `test_annotation_native_sources.py` | Persisted input mutations through real feed to decorated calls; corrupt source unavailable then recovered; snapshot immutability; missing database not created; byte/key bounds |
| `test_annotation_startup_lookup.py` | Mixed-profile decorated goal calls, ordering, atomic failure, exact authorization, registry replacement/disposal custody and profile-binding validation |
| `test_annotation_native_kanban.py` | Real board mutation to decorated annotation call; incomplete board unavailable; cross-board attention deduplication; strict external-source errors propagate |
| `test_annotation_native_runtime.py` | Two real authorized profile stores; all seven decorated families under request-I/O tripwires; readiness before drain; one drain/restart; restart only after every binding is legacy; unready runtime retains legacy and does not install |
| `test_annotation_registry_events.py` | Actual process registration/finalization wakes the source without file persistence; registry scope, disposal and callback failure isolation |
| `test_annotation_view_bridge.py` | Cold batch demands every missing row before returning unavailable and leaves rows untouched |
| `test_annotation_native_boot.py` | Injected decorated bootstrap reader is unwrapped; selector-only scoped identity retained; bodies discarded; legacy annotation not admitted; collector restored on failure |

The bootstrap test supplies fixture readers: the actual default profile routes and the complete `prepare` path are **untested by that test**. Production HTTP route admission, web lifespan activation, installed Desktop consumers, hosted process execution and live cross-profile rollback are **untested here**. Source capture fixtures do not prove full operational inventory/watch bounds or long-lived demand eviction. Live deployment is unproved.

## Applying

Use the **whole segmented [README Applying](README.md#applying) recipe**, with its integration bridges, repair and verification steps. This is an append-only dependent mirror, not a set of standalone `git am` instructions. Applying these preparatory ordinals alone does not activate native annotations; do not add an ad hoc startup call to infer release approval.

## Gotchas

- Exact-profile authorization is not fallback discovery. A missing named profile must stay unavailable, even if a default profile is healthy.
- Projection is request-memory-only when native custody is installed; operational readers and asynchronous provider lookup belong to the source owner, not a reducer.
- A strict source failure must not become fresh zero counts. Legacy compatibility and native readiness deliberately differ at that boundary.
- Capture budgets reject oversized/time-expired work, but this checkpoint does not settle every hard-bound/watch-inventory or resident demand-eviction acceptance requirement.
- One shared legacy producer cannot restart while any profile still consumes native facts. Per-binding rollback without the global fence violates that ownership rule.
- Bootstrap selector discovery is authorized pre-admission work. Its interception context must never escape into ordinary requests or retain response bodies.

## Why not a plugin

Plugins can provide operational evidence, but canonical annotation decorators, profile routing, fact-owner readiness and process-wide legacy custody span core request and lifecycle boundaries. Implementing a separate plugin cache would duplicate the very source/readiness rules this change centralizes and could not enforce atomic cross-profile rollback.

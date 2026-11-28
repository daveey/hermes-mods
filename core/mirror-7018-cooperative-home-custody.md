# Cooperative home custody: staged transfer protocol and live ingress fences

Patch coverage: 6965-7011 (47 ordinals: 6965, 6966, 6967, 6968, 6969, 6970, 6971, 6972, 6973, 6974, 6975, 6976, 6977, 6978, 6979, 6980, 6981, 6982, 6983, 6984, 6985, 6986, 6987, 6988, 6989, 6990, 6991, 6992, 6993, 6994, 6995, 6996, 6997, 6998, 6999, 7000, 7001, 7002, 7003, 7004, 7005, 7006, 7007, 7008, 7009, 7010, 7011).

## Behavior

A staged protocol for moving a *live* card's "home" board between machines cooperatively (the older dead-origin rehome cannot be used for live worker homes). The source labels the early commits as a dormant substrate: no production caller, Board ACK, release API, home rewrite or migration command at first, and the preflight doc states it is administrative maintenance, not automatic migration. Treat the protocol as **dormant**: the companion Board route is not implemented or activated by this change, and the only entry is an explicit operator module, `python -m hermes_cli.kanban_home_cli {source,destination,reconcile-precommit}` (added in 6976; not wired into the main CLI parser or any sweep, refuses to run when worker task environment variables are set, destination fixed to `hub`). The home-custody guards added to existing writers act only when a prepared/pending marker exists, so they are inert for ordinary cards. **One exception is not dormant:** 6977 changes `kanban_create` placement for every create that carries an explicit origin (see Composition).

- **Source preparation and STOP.** The source freezes the task with an immutable tuple under a durable mutation fence; a distinct atomic source-STOP receipt releases it after destination materialization. Exact uncommitted attempts are recovered durably (a Board definitive refusal gives a durable negative decision); abort uses the Board's authenticated exact-tuple decision and never infers non-commit from network errors.
- **Destination.** Verified durable materialization imports the manifest preserving event chronology (not UID-lexical order), probe host custody and scheduled rows; the native destination stays *ineligible* until the source STOP feed arrives.
- **Epochs.** Live-home replay epochs are captured at native writes (immutable sidecars for typed commands: retain, actionability, release, containment, fresh renewal), so an old serialized intent cannot be authorized by a later source lease.
- **Preflight.** Before committing local preparation the explicit source owner negotiates the Board's authenticated read-only `GET /v2/boards/<board>/home-transfer/capability` (response must be a JSON object whose `home_transfer` is the exact integer 1, bounded to 8192 bytes, not cached for later preparations; identity is validated before any schema, task or network effect); an absent/unsupported Board means no local preparation write at all. After preparation, failures retain the exact frozen attempt.
- **Composition.** Home custody is excluded across release producers, review startup, quarantine appends, maintenance, ignored commands, notice and CI poll carriers (frozen carriers are skipped before work), promotion, attachment deferral and candidate capture; late custody writers are fenced at native commit; first-install guards are retained; no-send custody waits keep provenance through delivery and maintenance callers. **Live (not marker-gated) change:** in `tools/kanban_tools.py::_handle_create`, an explicit-origin create now consults the canonical creation-home plan (`_canonical_origin_home`); when a plan exists (pool custody) the card is placed through the worker/pool home instead of origin-bound local storage, with origin authority carried unchanged by `_origin_route`; non-pool origins keep the old storage. Only the `kanban_create` tool handler is covered (`test_kanban_home_explicit_origin.py`: forwards worker home, never falls back to worker when not planned, non-pool keeps storage); other create entry paths (CLI, REST, execute_code wrappers) are **untested** by this series.

## How it works

`hermes_cli/kanban_home_{transfer,schema,generation,revision,command,cli,owner,probe,destination,feed,finalized,source_stop,abort,memo}.py` hold the protocol; many existing writers (`kanban_db_sync.py`, `kanban_db_promote.py`, `kanban_hold_*`, `kanban_containment_*`, `kanban_ask_resolved*`, `kanban_ci_progress.py`, etc.) gain a home-custody guard.

## Code

The `kanban_home_*` modules above plus the writer modules listed under Behavior, and `website/docs/developer-guide/kanban-home-preflight.md`.

## Tests

Read from source; **not executed here**. Named suites include `test_home_abort.py`, `test_home_admin_capture.py`, `test_home_attachment_archive_contracts.py`, `test_home_candidate_capture_composition.py`, `test_home_certification_finalization.py`, `test_home_owner_preflight.py` (real Hub HTTP transport against a loopback Board), plus legacy-corruption fixture migrations and a census sample test. The tracer used a disposable worker/HTTP Board, not a deployed one. Real live-home moves between production machines are **untested**; no rollout claim is made.

## Applying

Apply 6965-7011 in order as one unit; repeated review rounds correct earlier commits.

## Gotchas

- Capability is readiness evidence, not a commit; never clear a marker or change the nonce after a post-prepare failure.
- A frozen source rejects promotion/comments by design.

## Why core, not a plugin?

Kanban store/sync protocol.

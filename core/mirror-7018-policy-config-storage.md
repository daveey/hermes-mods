# Policy-config interning: exact-byte storage and opt-in rollout

Patch coverage: 6818, 6819, 6820, 6821, 6822, 6823, 6824, 6825, 6826, 6942, 6943, 6944.

## Behavior

Session local-policy receipts (stored in `state_meta` under `gateway.local_policy.v1:<session>`) can keep their `config_json` as an exact-byte, SHA-256-addressed blob (`gateway.policy_config.blob.v1:<digest>`) with per-owner reference keys (`...ref.v1:<digest>:<session>`); the receipt then holds `config_ref_v1` instead of `config_json`. Hashing is over the exact UTF-8 text, and reads verify the digest.

**Live code paths (wired in production, but writers stay inline unless the switch is on).**
- Readers: `local_receipt`, local-session commit retry, lineage advance and compression adoption, quarantine, branch, prepared-update snapshot and the profile-selection commit all hydrate through `load_receipt`/`decode_receipt`. `load_receipt` joins receipt and blob in one statement (one snapshot even on pooled autocommit readers); `decode_receipt` requires an open owner transaction. A missing or hash-mismatched blob, a malformed reference, or a receipt carrying both `config_json` and a reference raises a typed `storage_unavailable`; `RecursionError` from crafted deeply nested JSON is translated at decode and at stored-reference reads (6826).
- Writers: new-session creation (`commit_local_session`) calls `store_new_receipt`, which interns only when the receipt's own frozen config JSON has `sessions.intern_policy_configs` exactly boolean `true` (absent, malformed or truthy non-boolean values keep inline storage). Updates use `replace_receipt`, which keeps the owner's existing format; branching inherits the parent's format; quarantine edits only the stored metadata and preserves the stored representation and reference. Retirement and release go through `delete_receipt`, which removes the blob only after the last reference key is gone (a key-indexed `GLOB` check, no refcount). All store/delete helpers refuse an autocommit connection, use a savepoint and never commit the caller's transaction.
- Recovery: `session_recovery._finalize_derived_metadata` calls `reconcile_recovered_references` inside its immediate transaction. It validates every surviving receipt first, rebuilds all reference keys from them, then deletes blobs no surviving owner references (6820). Missing/corrupt blobs or malformed custody raise `SessionRecoveryError` so the recovery CLI fails closed (6825); the recovery output is kept separate.

**Rollout tool (6942-6944).** `python -m hermes_state_policy_rollout --db <state.db> --direction intern|inline [--apply] [--readers-compatible] [--after <session>] [--limit N] [--max-bytes N]` runs one finite batch through `convert_policy_config_batch`:
- Default is a metadata-only dry run on a read-only `SessionDB` (no payload validation, no DML). `--apply` runs one `_execute_write` owner transaction; a malformed or unreadable receipt rolls back the entire batch.
- Interning with `--apply` requires `--readers-compatible` (literal `True` at the function boundary); this records an operator acknowledgement and does not discover reader compatibility. Hydration (`--direction inline --apply`) does not need it.
- Defaults 8 keys and 1 MiB; ceilings 32 keys and 4 MiB; other values give `invalid_params`. Size accounting charges raw receipt bytes plus a worst-case (6x) estimate of the shared blob before hydrating; oversize owners are skipped and reported.
- Owners that are not idle (`session_busy`, `unknown_execution`, or a mutation-guard refusal) are skipped; receipts already in the requested format are not rewritten; no runtime revision, route or policy value changes.
- Output is JSON identifiers and counts only; the CLI prints fixed `invalid_params` or `storage_unavailable` errors (6944) and never exception text. `more=false` or `converted=0` is not downgrade readiness.
- Config: `sessions.intern_policy_configs` defaults to `False` in `DEFAULT_CONFIG`; 6943 documents it in the configuration guide and the design note `docs/designs/policy-config-storage.md` (rollout gates, hydrate before downgrade).

## How it works

`hermes_state_policy_config.py` (`store_receipt`, `store_new_receipt`, `replace_receipt`, `load_receipt`, `decode_receipt`, `delete_receipt`, `reconcile_recovered_references`, digest/reference helpers) and `hermes_state_policy_rollout.py` (`convert_policy_config_batch`, `main`). Consumers: `hermes_state_local.py`, `hermes_state_local_lineage.py`, `hermes_state_local_quarantine.py`, `hermes_state_mutation_branch.py`, `hermes_state_mutation_prepared.py`, `gateway/session_profile_selection.py`, `hermes_cli/session_recovery.py`.

## Code

The files above plus `hermes_cli/config_defaults.py`, `docs/designs/policy-config-storage.md`, `website/docs/user-guide/configuration.md`.

## Tests

Read from source; **not executed here**; no fresh run and no native or end-to-end green claim. `tests/hermes_state/test_policy_config_storage.py`, `test_policy_config_consumers.py`, `test_policy_config_rollout.py`, `test_policy_config_rollout_boundaries.py`, `test_policy_config_rollout_cli.py`; `tests/gateway/test_policy_config_profile.py`; `tests/hermes_cli/test_session_recovery_policy_config.py`. They cover default-inline, shared exact bytes and last-reference retirement, snapshot/transaction fences, typed missing/malformed/deep receipts, recovery rebuild and ownerless blob collection, the exact-boolean switch, bounded resumable conversion, skip of live/unknown owners, strict acknowledgement and CLI refusal labels. An actual migration of a populated production store and compatibility of every in-memory reader are **untested** here.

## Applying

Apply 6818-6826 then 6942-6944 in ordinal order.

## Gotchas

- Ensure every process that reads the state database understands the reference format before enabling interning; old in-memory readers cannot decode it. Downgrade requires disabling the switch, then hydrating all references with `--direction inline`.
- Disabling the switch does not rewrite existing interned receipts.
- Config key exactness: truthy non-booleans do not enable it.

## Why core, not a plugin?

Core session-state storage.

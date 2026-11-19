# Optional App inventory routing and PAT budget

These observe-only options do not change job dispatch, metadata discovery,
CI scale admission, or landing readers.

- Runtime `hourly_request_cap` caps total rolling-hour PAT/unknown-credential wire
  attempts across credentials, pages, redirects and retries, not refreshes. Each
  attempt is persisted before network I/O; missing/failing persistence fails closed.
  Validated installation credentials are exempt, never credentials named like Apps.
- `inventory_credentials` maps exact `repository:owner/name` or `organization:name`
  keys to existing broker refs. The loader validates App coverage. Only enumeration
  uses these routes; original discovery and dispatch refs are preserved.
- Broker credentials can carry `app` with `app_id_env`, `private_key_env`,
  `installation_id`, `repositories`, `organizations`. These describe existing grants,
  not permission to expand an App. Read-only tokens are minted in-process, with
  returned grants and expiry checked. No implicit PAT fallback. A renamed App repo
  returns scope-local `moved`, without numeric alias follow or sibling backoff.
- Collector and maintenance must share one runtime, state directory and OwnerLock.
  Never implement the shared budget with independent per-worker caches.
- Cached `inventory_cache` reports `fetched_at`, `age_s`, and `incomplete`.
  Deferrals retain previous evidence, never imply fresh complete coverage, and log
  reset time. Legacy ledgers fence the first hour conservatively; a restart before
  first persistence can extend that fence.

## Required rollback order

The cap adds `@global-hourly-attempts` to the provider budget record. **Pre-feature
code cannot read it** and will crash collector/maintenance during restore. Back up
runtime and state before activation. Never delete the database or unrelated quotas.

1. Quiesce collector AND maintenance through their supervisors; verify no writer
   remains. Keep both stopped through migration.
2. With new code still installed, validate a rollback runtime removing
   `hourly_request_cap`, `inventory_credentials` and added App credentials. Preserve
   original credentials, discovery/dispatch refs and unrelated settings. Restore a
   preactivation runtime only if this does not discard later legitimate edits.
3. Under the shared OwnerLock, load the existing provider budget using the new
   adapter **without a cap**, then persist `export_state()` through the collector's
   normal state sink. This removes the attempt record while preserving all other
   budget rows. Read the persisted record back and verify the reserved key is absent.
   Do not make a network refresh just to force a write, or edit SQLite behind writers.
4. Only once runtime and ledger are compatible, revert the consumer code through
   reviewed deployment. Restart original supervisors and verify successful restore
   and publication from both collector and maintenance. Keep backups until then.

Re-enabling the cap applies the conservative legacy fence again. This rollback
never reverts ETag transport or changes landing intervals; it proves no savings.

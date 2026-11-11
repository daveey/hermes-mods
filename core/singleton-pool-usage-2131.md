# Singleton credential pools keep their account identity

Mirror ordinal: **2131**.

## Behavior

Model options expose `usage.accounts` for every nonempty persisted credential pool, including a pool with one account. Missing, stale, empty or failed telemetry no longer erases that account's identity or converts it into a provider-wide legacy gauge. An unknown account is not advertised as ready or as having all quota left.

Providers with no persisted accounts, including an empty pool, retain the previous `usage.windows` behavior. Existing authentication/custom-provider eligibility checks remain in place. This changes usage presentation/refresh identity, not credential selection or account readiness policy.

## How it works

`inventory._apply_usage` now tests membership in the set of nonempty persisted pools, rather than requiring a pool size of at least two. Pooled rows always go through `_pool_usage_accounts`, retaining each account's fingerprint/label and state. Only nonpooled providers use ambient legacy credentials for the legacy gauge.

Pooled telemetry refresh requests use explicit per-entry credential identity. The provider-wide ambient refresh request is no longer additionally queued for pooled providers. An unavailable refresh leaves the row unknown with no fabricated windows; a model-specific cooldown remains separate from account-wide readiness. Reading model options does not rewrite the credential file.

Desktop already consumes account-shaped usage; the new chip regression verifies the sole unknown account remains labeled, shows unavailable usage and does not imply a ready/full balance.

## Code

- `hermes_cli/inventory.py`: nonempty-pool classification and removal of pooled provider-wide refresh enqueue.
- `tests/hermes_cli/test_model_options_single_pool_usage.py`: singleton, multiple-account and no-account controls.
- Existing account/pool usage tests update singleton expectations.
- `apps/desktop/src/components/provider-status-chip.test.tsx`: sole-unknown-account display regression; this commit changes its tests, not the chip implementation.

## Tests

Source tests were inspected, **not run here**.

`test_model_options_single_pool_usage.py` covers absent/empty pools with absent/stale/fresh legacy telemetry; one/two persisted accounts with absent, stale, empty, fresh or dead telemetry; explicit singleton refresh with success/failure and exact credential use rather than ambient credentials; credential-file byte stability; and model cooldown not implying account readiness.

`test_model_options_account_usage.py` asserts unknown-to-ready refresh retains the same account ID. `test_model_options_pool_usage.py` retains the no-pool legacy gauge control. The Desktop chip test checks the sole account label, unavailable message and lack of ready/100%-left text rather than treating unknown as a usable quota balance.

These are source regression contracts, not fresh subscription API calls or a live Desktop screenshot.

## Applying

Apply the complete snapshot via [README.md](README.md)'s replay recipe. Restart the backend serving model options; build/restart Desktop only through the normal supported workflow if needed. Run the listed Python usage tests and Desktop chip test in disposable environments with lockfile-matched dependencies. No credentials were read, refreshed or modified against live accounts for this doc.

## Gotchas

- One persisted account is still a pool identity. Cardinality must not select the ambient legacy refresh path.
- Unknown telemetry is not zero usage, full quota or account readiness.
- Do not average sibling accounts into a provider-wide percentage.
- Empty/no pool remains a deliberately different compatibility path.
- Model cooldowns describe a model constraint, not proof that the entire account is healthy or unavailable.

## Could this be a plugin?

A usage provider plugin can supply account telemetry. The model-options inventory chooses the wire shape and refresh identity shared by callers, so this cardinality correction belongs in core rather than a Desktop-only display workaround.

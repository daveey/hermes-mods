# Live credential pool adopts pushed tokens for no-refresh OAuth rows

Patches:

- `patches/0160-fix-credential-pool-live-pool-adopts-pushed-tokens-f.patch`: `_adopt_pushed_norefresh_row` re-reads the persisted row and adopts a newer access token.

What you see: a long-lived gateway picks up a token a hub-side minter pushed to its pool store without a restart, instead of keeping the expired or 401-benched copy.

## Behavior

- A minter writes `{access_token, expires_at_ms}` OAuth rows with no `refresh_token` into a home's pool store. Such a row can never refresh itself, so a live pool kept the old token until a fresh `load_pool()`.
- Now `_refresh_entry` and the exhausted-entry recovery path re-read the exact persisted row under the cross-process auth-store lock; if its `access_token` differs, the pool adopts it and clears the status. Rows with a refresh_token, borrowed sources and token-less disk rows are left alone.

## How it works

- `agent/credential_pool.py`: `CredentialPool._adopt_pushed_norefresh_row(entry)`; called at the top of `_refresh_entry` and in the exhausted/dead recovery branch. Failures are logged at debug and return the entry unchanged.

## Code

`agent/credential_pool.py`.

## Tests

`tests/agent/test_credential_pool_norefresh_adopt.py`: valid no-refresh rows are selected; an expiring row is skipped, not exhausted; the live pool adopts a pushed token (expiring row, 401-benched row, forced refresh; Codex JWT too); an unchanged disk token is not adopted.

## Applying

Standalone. Useful with a token-minting hub (see hermes-cred-relay).

## Gotchas

- Adoption needs the minter to write into the same home the pool reads.

## Plugin?

No. The pool's refresh path has no plugin seam.

## Follow-up patch (1580): pushed tokens keep a live account quota mark

**Patch:** `patches/1580-daveey-Preserve-live-account-quota-across-pushed-OAu.patch` (`311d8339309`, `Preserve live account quota across pushed OAuth tokens`).

**Behavior.** A pushed token repairs authentication, not an account's quota window. When the persisted row being adopted is `exhausted` with a future `last_error_reset_at`, a rate-limit-shaped error, and a code other than 401/403, the pool adopts the new token but keeps the status, reset, error code and model cooldowns, and does not record a status clear. A 401/dead row or an expired quota mark is still adopted and cleared to OK as before. `_refresh_entry` returns None (no lease) when the adopted row is not OK, so `select()` and forced refresh do not hand out a quota-walled account.

**How it works / Code.** New `agent/credential_pool_pushed.py::has_live_quota_mark(entry)` (uses `_parse_absolute_timestamp` and `auth_codex._is_codex_rate_limit_shaped`). `agent/credential_pool.py::_adopt_pushed_norefresh_row` adopts `stored` unchanged when the mark is live, else `replace(stored, **_MARK_OK)`, and persists `status_cleared_ids=[]` in the keep case.

**Tests.** `tests/agent/test_credential_pool_pushed_quota.py`: `test_pushed_token_quota_policy` (429 live reset kept and unavailable; 401 dead cleared; 429 past reset cleared; model cooldowns retained in all cases) and `test_refresh_does_not_lease_pushed_quota_row` (`refresh` and `select` paths return None and keep the reset). This mirror did not run these tests.

**Applying / Gotchas.** Depends on the preceding series; apply the full ordered series per [README.md](README.md#applying), standalone not certified. The mark is kept only when the row has an absolute future `last_error_reset_at`; an exhausted 429 row without a parseable reset is still cleared on adoption. Only the Codex row is tested for the keep case; the Anthropic row is tested through refresh/select.

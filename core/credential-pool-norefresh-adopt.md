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

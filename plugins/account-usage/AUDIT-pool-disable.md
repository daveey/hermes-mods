# Core `hermes auth disable` compatibility audit

Candidate: daveey/hermes `10c007a5706` (worktree `pool-disable-accounts`, 4 commits), parent
`9df7575e561` (already accepted: every execution pin matches).

| File | Candidate SHA256 |
|---|---|
| `agent/credential_pool.py` | `bbc6ef330a04a154f98ef0d6f128430ebe756fb945f56764db9d1ee18cce35eb` |
| `agent/client_lifecycle.py` | `c3059954c25cc4decbacd2d4172a952f16368e1e062ea6bb8f57ccf1ff58f260` |

## Findings

- `credential_pool`: the one-line diff makes `_available_entries` iterate `self._enabled_entries()`
  (rows minus those listed in auth.json `disabled_credentials`) instead of `self._entries`. With no
  disabled rows the list is identical, so select/peek/rotation/exhaustion/model-cooldown behaviour is
  unchanged; a disabled row is simply never available. `recover_with_credential_pool`, the client
  factories and request-local snapshot seams are untouched.
- `credential_pool` also: `_try_refresh_current_unlocked` returns None for a disabled current row (so
  `try_refresh_current`/`try_refresh_matching` never refresh it; recovery then rotates through
  `select`), and `acquire_lease(id)` of a disabled row falls through to normal lease selection.
  `_is_sole_credential` and `next_available_at` count/scan enabled rows only.
  No-disable behaviour is identical.
- `client_lifecycle`: `_create_request_openai_client` first calls `rebind_off_disabled(self)`, a no-op
  unless the session is Codex/xAI, its pool has a disabled record, and the bound row is disabled; then it
  adopts `pool.select()`'s token through the existing `_adopt_openai_credentials` (AuthError when none
  is left). The request-local slot checkout, client kwargs and the plugin's request-scoped snapshot
  seams are unchanged; a manual pick of a disabled account is already refused in `choose_snapshot`.
  `_try_refresh_anthropic_client_credentials`: when re-resolution yields no token and the current key is
  a disabled row's, `refuse_disabled_token` raises AuthError; otherwise the old `return False` path.
- New file `credential_pool_disabled.py` and the small edits to `credential_pool_admin.py`,
  `credential_pool_pin.py` and `credential_pool_model_cooldowns.py` are not pinned seams. The
  cooldown change makes `token_is_blocked` true for a disabled account's token, which the execution
  plugin already treats as "do not send".

## selection.py change (fast-rate-limit-fallback composition digest)

`choose_snapshot` now filters the matched account's rows through core `enabled_entries` (ImportError =
older core = no filter) and raises `SelectionRejected('Account is disabled…')` when none remain. This
only narrows the candidate set before the existing route/terminal checks; the recovery wrapper and
the fast-rate-limit-fallback composition contract are unchanged, so the composition digest is moved
to the new file hash `100c74901bfa…`.

## Verification

`check_compat.py --core <candidate>` clean; account-usage suite against the candidate core and against
its parent (see commit message).

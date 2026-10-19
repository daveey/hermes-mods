# Auxiliary 429s bench one model, and usage rows say why an account is limited

Patches:

- `patches/0133-fix-auxiliary-pool-rotation-names-the-failed-wire-mo.patch`: agent. Auxiliary calls (goal
  judge, titles, compression, vision...) tell the credential pool which model and which key failed.
- `patches/0134-feat-inventory-usage-accounts-say-why-they-are-limit.patch`: inventory + gateway contract. Each
  pooled account row in the `model.options` payload says why it is limited, until when, and which models alone
  are cooling down.
- `patches/0144-fix-inventory-a-duplicate-login-s-stale-bench-no-lon.patch`: inventory fix-up. When one account
  is signed in twice, a leftover bench on one credential no longer paints the account red while its live usage
  window shows quota left and the other credential is usable.

What you see: one aux 429 on a Claude account no longer takes that account out of rotation for every model for
an hour. Before this fix, the account-usage chip then showed a red dot while the account still had quota. Now the
chip (desktop plugin `account-usage` in this repo) shows red only for an account that is really out of quota and
amber for a temporary cooldown. Each row says why ("cooling down · rate limited · back 23m"). A Codex
`usage_limit_reached` with the provider's reset date stays red until that date.

## Behavior

- **Generic Anthropic 429 on an auxiliary call (sync `call_llm` and async `async_call_llm`):** the pool benches
  only that (credential, model) pair. The credential keeps serving other models. Before the fix the call reached
  `mark_exhausted_and_rotate` with no `model`, so the pool benched the whole credential for an hour.
- **Billing / quota 429, 402, 401 on an auxiliary call:** still account-wide. The aux path now passes
  `failure_reason="billing"` for payment errors and `"rate_limit"` for plain 429s. Auth errors keep their 401 path.
- **The rotated retry fails too:** the second rotation names the key that served the retry (the rotated
  credential) and its wire model. Before the fix it named no credential, so the pool benched whatever `current()`
  was by then, or nothing after another process rotated.
- **Usage rows (`_apply_usage`, the builder of the `model.options` provider rows):** `state` keeps its meaning
  (`limited` still blocks selection). New sanitized fields: `limit_kind` (`quota` | `cooldown`, only on limited
  rows), `reason` (`quota_window`, `billing`, `usage_limit`, `rate_limit`, `auth`, `cooldown`), `cooldown_until`
  (ISO expiry of a live credential-wide cooldown) and `model_cooldowns` (`[{model, until}]`, live per-model
  cooldowns, with year-long entitlement benches left out). An exhausted account window outranks a cooldown
  (`quota`). No provider message, key or URL rides the wire, and the read path never writes `auth.json`.
- The `model.options` RPC wire shape is declared in the contract (`ProviderUsageAccount`,
  `ProviderUsageModelCooldown`) and the generated contract files. The RPC round-trip itself is untested here; the
  tests call `_apply_usage` directly.

## How it works

- `agent/auxiliary_client.py`:
  - `_recover_provider_pool(provider, exc, *, failed_api_key, failed_model)` passes `model=failed_model` and a
    classified `failure_reason` to `pool.mark_exhausted_and_rotate`. The pool's existing
    `_is_model_scoped_failure` (in `agent/credential_pool_model_cooldowns.py`) does the scoping: Anthropic + 429 +
    a model + not billing gives a model cooldown.
  - `_retry_same_provider_sync/_async` stamp `(api_key, wire model)` of the request they sent on the exception
    (`_stamp_failed_identity`, attribute `_hermes_aux_failed_identity`). `_ladder_credential_rungs` reads it with
    `_failed_identity(exc, client, kwargs)`. When there is no stamp it falls back to the original client's key and
    `kwargs["model"]`. That covers both the initial failure and a failure after the auth-refresh retry.
- `hermes_cli/inventory.py::_pool_usage_accounts`: `_cooldown_cause(entry)` classifies a live cooldown from the
  persisted classified fields only (`last_error_code`, `failure_reason`, `last_error_reason`): a 402 or `billing`
  is quota/billing, a quota reason code (`usage_limit_reached`, `insufficient_quota`, ...) is quota/usage_limit,
  401 or auth is cooldown/auth, and a 429 is cooldown/rate_limit. `_live_model_cooldowns` lists live
  model cooldowns shorter than half the entitlement bench (the same cut `limit_state` uses). Dedupe of one account
  seen under two credentials prefers a `quota` row over a `cooldown` row. Since 0144 a `ready` row of the same
  account wins over a `limited` one (rank `ready > limited > unknown > unavailable`). Both rows share the account's
  usage windows, so `ready` means the live windows have quota left and that credential is usable. Before 0144 an
  old Codex `usage_limit_reached` on a duplicate login showed a Pro account as out of quota at 30% weekly use.
- `tui_gateway/contracts/config_free_tier_control.py`: additive optional fields; contract files regenerated.

## Tests

- `tests/agent/test_auxiliary_pool_model_scoped_rotation.py` (new):
  - `test_initial_429_reports_failed_model_and_credential[sync|async]`: real `call_llm` / `async_call_llm` ladder;
    the rotation gets `model`, the failed key and `failure_reason="rate_limit"`.
  - `test_rotated_retry_failure_reports_the_rotated_credential[sync|async]`: the second rotation names the rotated
    key (`KEY_B`) and the model.
  - `test_billing_429_is_reported_as_billing[sync|async]`.
  - `test_model_a_429_does_not_bench_model_b_on_a_real_pool`: real two-key pool on a temp `auth.json`; model A is
    benched on seat A, model B still selects seat A, and the credential-wide status is untouched.
  - `test_billing_and_auth_stay_account_wide_on_a_real_pool[429 billing|402|401]`.
- `tests/hermes_cli/test_model_options_pool_usage_reasons.py` (new, real auth stores, `_apply_usage`): live
  cooldown under 100% (amber cooldown with expiry, no secret message on the wire), expired cooldown (ready, no
  cause), exhausted window (quota), window outranks cooldown, billing cooldown (quota), model-only cooldown (ready
  plus `model_cooldowns`, entitlement and expired ones left out), Codex explicit `usage_limit_reached` reset
  (quota until the reset), dead row (`reason: auth`). Every case also asserts `auth.json` is byte-identical.
- Same file, 0144: `test_duplicate_login_with_live_quota_left_shows_ready[stale first|second]` (two credentials
  of one Codex account, one benched by `usage_limit_reached`, live window 30% → one `ready` row);
  controls `test_duplicate_login_without_live_usage_keeps_the_limit` (no live usage → `limited`/`quota`) and
  `test_duplicate_login_with_exhausted_window_stays_red` (100% → `limited`/`quota`), both orders. The new test
  failed before the rank change and passed after; the controls passed both times.
- Both files were RED before the production change and GREEN after it.

## Applying

0133 is standalone. It needs `mark_exhausted_and_rotate(..., model=, failure_reason=)` and the model-cooldown
mixin (`agent/credential_pool_model_cooldowns.py`), both present upstream. 0134 needs the pooled
`usage.accounts` rows from `_pool_usage_accounts`. Regenerate the contract files after applying
(`scripts/gen_gateway_contracts.py`).

## Gotchas

- Persisted benches written before 0133 stay until they expire (1 h for a plain 429). No auth state is reset.
- A payment-flavoured 429 on a sole credential now gets the full billing bench rather than the short sole-key
  bench, which matches the main agent's classification.
- 0144 only changes the display. The pool already skips the benched credential and uses the healthy one. The
  stale bench stays on that credential until it expires, or `hermes auth refresh` clears it, or the duplicate is
  removed (`hermes auth remove <provider> <id>`).
- `limit_kind` is absent from older backends. The plugin then keeps a limited row red, so it never shows green
  for a live restriction.

## Plugin?

The display half is a plugin (`desktop-plugins/account-usage`). The cause fields need the pool's private state,
which only the inventory builder reads. The aux rotation sits inside the recovery ladder, where no plugin hook
exists.

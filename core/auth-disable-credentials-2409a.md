# Disable and re-enable credential accounts

Mirror entries: [2288](patches/2288-daveey-feat-auth-hermes-auth-disable-enable-keeps-a-pooled-credential-from-being-used.patch), [2289](patches/2289-daveey-fix-auth-disabled-accounts-are-refused-on-the-singleton-aux-env-token-and-bound-session-paths.patch), [2290](patches/2290-daveey-fix-auth-disabled-Codex-xAI-accounts-are-refused-at-the-singleton-resolvers-slot-rows-match-b.patch), [2291](patches/2291-daveey-fix-auth-disables-never-stick-to-a-reused-counter-slot-label-remove-forgets-wake-times-skip-d.patch), [2292](patches/2292-daveey-fix-auth-disables-match-per-record-by-id-label-workspace-account-or-secret-fingerprint.patch), [2293](patches/2293-daveey-fix-auth-Codex-catalog-service-tier-and-model-setup-probes-never-send-a-disabled-account-s-to.patch), [2294](patches/2294-daveey-fix-auth-Codex-seats-match-by-workspace-user-borrowed-sources-stay-refused-after-they-refresh.patch), [2295](patches/2295-daveey-fix-auth-older-workspace-only-Codex-records-still-match-only-the-Claude-Code-login-is-a-disab.patch), [2296](patches/2296-daveey-fix-auth-an-env-var-row-s-disable-means-its-value-not-the-slot.patch), [2297](patches/2297-daveey-fix-auth-the-Codex-service-tier-lookup-refuses-a-passed-in-disabled-token-too.patch).

## Behavior

- hermes auth disable PROVIDER TARGET retains a credential but excludes it from use; enable reverses the active-store disable. Targets are an index, entry ID or exact label.
- Supported disable providers are anthropic, openai-codex and xai-oauth. Selection, rotation, refresh/lease, bound-session and delegated-child paths skip disabled accounts; singleton, auxiliary and environment-token paths also check them.
- Root-home disables apply to profiles; a profile enable warns if the root still disables the account. Status marks disabled entries and availability calculations ignore them.
- Codex catalog, service-tier and model-setup probes refuse disabled tokens, including explicitly passed service-tier tokens.
- Identity matching separates reused slots, Codex workspaces and Team seats; environment-row disables follow the token value, while the Claude Code borrowed login remains a disabled login slot even after external refresh.

## How it works

The locked disabled_credentials section in auth.json is separate from pool rows, so stale pool flushes do not erase it. Readers union active/root records and cache file identity using mtime, size and inode. Final matching is record-by-record by ID, non-generic label, JWT workspace/user identity, non-reversible token fingerprint or the Claude Code source. Contradictory account identities prevent namesake/reused-slot matches; legacy workspace-only Codex records still match their seats. Env rows with changed token fingerprints stop matching even if their ID is reused. Removal forgets an active disable only when no remaining matching account needs it. Client checkout rebinds a disabled bound Codex/xAI session to an enabled row, or refuses if none is usable; Anthropic pin resolution similarly stops reuse.

## Code

- `agent/credential_pool.py`
- `agent/credential_pool_admin.py`
- `agent/credential_pool_disabled.py`
- `agent/credential_pool_model_cooldowns.py`
- `agent/credential_pool_pin.py`
- `hermes_cli/auth_commands.py`
- `hermes_cli/subcommands/auth.py`
- `agent/anthropic_credentials.py`
- `agent/auxiliary_client.py`
- `agent/client_lifecycle.py`
- `hermes_cli/runtime_provider.py`
- `hermes_cli/auth_codex.py`
- `hermes_cli/auth_xai.py`
- `tools/delegate_tool_child_run.py`
- `agent/codex_service_tiers.py`
- `hermes_cli/codex_models.py`
- `hermes_cli/model_setup_flows.py`

## Tests

Source test references below are inspection evidence only. **Not rerun by this mirror documentation pass.** No live/runtime or CI-green claim is made.

- `tests/agent/test_credential_pool_disabled.py` — `test_disabled_row_is_never_selected`, `test_all_disabled_means_no_available_entry`, `test_enable_restores_selection`, `test_set_disabled_is_idempotent` (additional cases in this file).
- `tests/hermes_cli/test_auth_disable_command.py` — `test_disable_enable_round_trip`, `test_unknown_target_exits`, `test_enable_warns_when_root_disables`, `test_remove_forgets_the_disable_so_a_re_added_row_is_not_born_disabled`.
- `tests/agent/test_credential_pool_disabled_paths.py` — `test_token_disabled_matches_only_disabled_rows_and_is_free_when_nothing_disabled`, `test_singleton_runtime_of_a_disabled_account_raises`, `test_auxiliary_codex_singleton_token_of_a_disabled_account_is_dropped`, `test_anthropic_token_of_a_disabled_account_is_dropped_even_without_a_model` (additional cases in this file).

## Applying

Apply these entries only as part of the ordered mirror series using [README Applying](README.md), including its replay bridges and required post-replay overlays. They are not independent cherry-picks. This documentation pass did not replay or install the series.

## Gotchas

Disabling is not deletion, logout, token revocation or an organization-wide access ban; an in-flight request is not recalled. Only supported providers expose the disable guarantee; unsupported ones must use removal. The Claude Code slot exception intentionally refuses a later account logged into that same borrowed source until enable; an environment variable does not have that slot behavior. Read-only status can inspect disabled credentials without spending a refresh grant. Do not copy auth files or token examples into public docs. No live credential was modified and no provider request was made by this pass.

## Plugin

No. This changes core behavior or its regression infrastructure; a plugin cannot enforce the same internal invariants.

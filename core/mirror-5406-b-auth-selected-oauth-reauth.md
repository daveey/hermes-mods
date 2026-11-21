# Auth: atomic selected-row OAuth recovery (`auth reauth`) and Codex device sign-in bound to a row

Mirror entries 5209, 5210.

## Behavior

- `hermes auth reauth PROVIDER CREDENTIAL_ID` is an interactive command for an exact locally owned pool id (providers `anthropic` and `openai-codex`; labels and indexes are not accepted). It replaces the row instead of adding a second account. Supported sources: Anthropic `manual:hermes_pkce`, Codex `manual:device_code` and `manual:loopback_pkce`; borrowed, environment, singleton `device_code` and external-store rows are refused, not copied.
- The row must still exist, be enabled and be unchanged when login finishes (id, label, priority and unrelated metadata preserved; login runs without the auth-store lock). Codex login must match both stored account and workspace principal. Anthropic code entry is masked; if echo cannot be disabled it stops. An interrupt during persistence reports an unverified outcome. Output never leaks the exchange error.
- 5210: dashboard `/start` accepts `{credential_id}`; the session is bound to the owned row via `prepare_reauth` before any provider call and approval commits via `commit_reauth` under the cancel lock. Wrong principal, changed/deleted row or unowned source is refused without a write and never falls back to an untargeted login; poll reports `credential_id` and `identity_verified`.

## How it works

- `hermes_cli/auth_reauth_target.py` (`prepare_reauth`, `commit_reauth`, principal and generation checks), `auth_reauth_command.py`, `auth_commands.py`, `subcommands/auth.py`, `agent/anthropic_credentials.py` (injectable code reader), `web_routers/oauth.py`; doc `website/docs/user-guide/selected-account-recovery.md`.

## Code

- `hermes_cli/auth_reauth_target.py`
- `hermes_cli/auth_reauth_command.py`
- `hermes_cli/auth_commands.py`
- `hermes_cli/subcommands/auth.py`
- `agent/anthropic_credentials.py`
- `hermes_cli/web_routers/oauth.py`

## Tests

Source tests (not executed in this mirror pass): `tests/hermes_cli/test_auth_reauth_command.py`, `test_auth_reauth_target.py` (replacement without duplicate, changed target refuses, unowned sources, principal and workspace must match, incomplete or disabled grant, single-use committed target, target repr hides credentials), `test_web_oauth_targeted_reauth.py` (start binds row, untargeted unchanged, bad target never falls back, row changed during sign-in, cancel during poll, second session cannot overwrite). Real provider sign-in is untested.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Completion confirms persistence, not provider health.
- Do not run in an agent-readable or recorded terminal.

## Why core instead of a plugin

Replacement must happen inside the core credential-store lock and CLI/dashboard auth surfaces.

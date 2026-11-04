# Credential pool: probe-restored Codex quota persists across reloads

Patches: **1903** (`83614574cf`, `fix(credential-pool): persist probe-restored Codex quota across reloads [daveey]`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- A probe recovery keeps the superseded failure generation on the ok row, and the auth-store merge lets an ok row stamped at or after a stale writer's exhaustion win: a fresh reload reads the account as ok, a stale writer cannot write the exhaustion back, and a later 429 still binds. Tokens, eligibility and the probe itself are unchanged.

## How it works

See Code; the merge moved to `hermes_cli/auth_pool_merge.py` (re-exported by `hermes_cli.auth`).

Source commit notes (first paragraph of each message, privacy-normalized):

- 1903: A successful Codex /usage probe (#43747) cleared the cooldown in memory with last_status_at=None. The auth-store recency merge read the missing stamp as epoch 0, judged the still-future on-disk exhaustion newer, and kept it: the probing process served the account while disk and every fresh process stayed exhausted. Reproduced on installed core 73ee883f (hub + mini).

## Code

`agent/credential_pool.py`, `agent/credential_pool_timestamps.py`, `hermes_cli/auth.py`, `hermes_cli/auth_pool_merge.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/agent/test_credential_pool_codex_probe_generation.py`: `test_probe_restored_quota_persists_and_survives_fresh_reload`, `test_probe_still_exhausted_keeps_cooldown_on_disk_and_reload`, `test_newer_concurrent_429_beats_probe_restored_quota`, `test_stale_writer_cannot_resurrect_the_superseded_cooldown`, `test_borrowing_profile_recovery_writes_root_row_and_survives_reload`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.

# Sessions: per-source retention

Mirror snapshot 5406; ordinals 5368.

Entries:

- **5368**: feat(sessions): per-source retention (sessions.source_retention_days)

## Behavior

- `sessions.source_retention_days` maps source to days; only unattended sources (cron, kanban, subagent, tool, recovered) are eligible, interactive sources are refused, invalid values are skipped with a warning. Same guards as auto-prune (pinned/mid-turn spared, stale open rows closed first), own throttle key, no VACUUM.
- CLI start and gateway housekeeping share `run_sessions_retention()`.

## How it works

- `hermes_state_maintenance.py`, `cli_auto_maintenance.py`, `config_defaults.py`, `gateway/run.py`, sessions guide.

## Code

Shipped non-test paths (union over these entries):

- `gateway/run.py`
- `hermes_cli/cli_auto_maintenance.py`
- `hermes_cli/config_defaults.py`
- `hermes_state_maintenance.py`
- `website/docs/user-guide/sessions.md`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/gateway/test_housekeeping_profile_scope.py`
- `tests/hermes_state/test_source_retention.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Disabled by default; deletion is irreversible.

## Why core instead of a plugin

Session store maintenance is core.

# Kanban links: `remote_links_and_origin_rows` forwards `stale_ok`

Patches: 2808.

- **2808** (`bf8cd31e5f`, fix(kanban): remote_links_and_origin_rows takes stale_ok for display routes)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- `remote_links_and_origin_rows(stale_ok=False)` now forwards `stale_ok` to `_links_entry`. The default stays strict (chat closeout needs read-your-writes). A warm stale entry returns before any fingerprint or lock work; a cold scope still projects synchronously.
- `scripts/run_tests.sh` forwards `HERMES_TEST_USER_STEPS_API` through its `env -i` so the stale-route test can target a candidate plugin checkout (it was silently a no-op).

## How it works

- `hermes_cli/kanban_session_activity.py`, `scripts/run_tests.sh`.

## Code

- `hermes_cli/kanban_session_activity.py`
- `scripts/run_tests.sh`

## Tests

- `tests/hermes_cli/test_kanban_links_stale_scopes.py`: `test_history_rows_stale_read_returns_while_fingerprint_is_held`, `test_history_rows_default_stays_strict`.

Test files touched by these patches:

- `tests/hermes_cli/test_kanban_links_stale_scopes.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- The slow route was a user-steps desktop plugin; this patch is the core API it calls, so a plugin must pass `stale_ok=True` to benefit.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

Core API change enabling a plugin route (user-steps) to stay stale-only; the plugin-side use is outside this patch.

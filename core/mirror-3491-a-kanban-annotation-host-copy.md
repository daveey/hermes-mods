# Kanban session-list annotation: shared host copy

Patches: **3482**.

## Behavior
- The session-list annotation for a scope is computed once per host and shared: a refresh publishes `(scope, fingerprint, read_at, parts)` to `<home>/cache/kanban-annotation/<scope>.host.json`; other web-server processes and the gateway adopt it instead of recomputing.
- A copy is adopted only on an exact fingerprint match and a read age under 30 s (dated by the copy's own read). Otherwise the process refreshes as before and publishes.
- The fingerprint covers the scope, board/replica/sync content keys (made portable; unreadable or connection-keyed inputs mean no fingerprint), the core sha, code identity of done checks and waiting sources (including plugin package file stats), and declared waiting-source inputs.
- `register_waiting_source(name, fn, inputs=None)` lets a source declare a JSON key that changes whenever its answer could. If any source declares none, the adopter takes the counts but re-runs every waiting source itself.
- One holder process per home (lease `kanban-annotation`, started with the annotation prewarm for roles serve/dashboard) refreshes each scope polled in the last 60 s when the copy belongs to other inputs or is 20 s old.
- Kill switch: `HERMES_ANNOTATION_HOST_COPY=0` turns publishing and adopting off.
- The disk copy encoding now keeps lists and nested dicts (execution evidence); older copies still decode.

## How it works
3482 adds the `kanban_annotation_host` module (publish, adopt, fingerprint, holder loop), moves `_read_annotation_parts` into it (the name stays bound in `kanban_session_activity`, so monkeypatches still apply), extends the disk encoding, and starts the holder from the web server app.

## Code
- `hermes_cli/kanban_annotation_disk.py`
- `hermes_cli/kanban_annotation_host.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/web_server_app.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_annotation_host.py`: `test_another_process_adopts_the_copy_on_an_exact_fingerprint`, `test_adopted_rows_equal_a_local_refresh`, `test_a_waiting_source_change_defeats_adopt`, `test_an_undeclared_waiting_source_is_rerun_by_the_adopter`, `test_a_stale_copy_is_ignored`, `test_a_code_sha_change_defeats_adopt`, `test_a_board_write_defeats_adopt`, `test_another_plugins_code_defeats_adopt`, `test_kill_switch_turns_publishing_and_adopting_off`, `test_unportable_inputs_publish_no_fingerprint`, `test_host_copy_round_trips_nested_evidence`, `test_holder_refreshes_a_demanded_scope_once_per_change`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A waiting source that declares no inputs is never served from another process; its part is recomputed by every adopter until the source declares inputs.
- Holder lease behavior across real processes is untested here; the test covers a single-process holder pass.

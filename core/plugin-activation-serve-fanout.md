# Plugin activation reaches every chat-hosting backend

Patch: **1629** (`9744578a42`, `fix(plugins): plugin activation reaches every chat-hosting backend, not just the host owner`).

Builds on the backend-activation reporting described in [mirror-runtime-hardening-1302.md](mirror-runtime-hardening-1302.md).

## Behavior

- A host can run a host-owner `hermes serve` (record role `serve`) and the Desktop's child backend (`desktop-serve`)
  at once. Desktop chats live in the child. `notify_serve_backend` used to dial only the first live record (host owner,
  else Desktop child), so after `hermes plugins install/enable` the Desktop child kept running the plugin hooks it
  loaded at start, while the CLI reported the host owner's success.
- Activation now POSTs `/api/dashboard/agent-plugins/activate` to **every** live, token-consistent record (host owner
  first, then Desktop child), deduplicated by `(pid, port)`.
- A dead record (stale pid), a record whose token no longer matches (torn restart), an unreadable record, or an
  unreachable port never blocks the other backend.
- The folded answer is the first backend that answered (host owner first), plus a `backends` list with each backend's
  `role`, `port`, `pid`, `create_time` and `answer`. When any backend failed, `backend_error` names it
  (`<role>:<port> <reason>` when there is more than one backend), so the CLI hint never reports a failed Desktop backend
  as loaded. No record still returns `None`.

## How it works

`hermes_cli/plugins_activation.py`: `serve_backend_records()` reads both host-rendezvous roles, skipping unusable ones.
`notify_serve_backend_record(record, name, home)` does the single POST (the old body, never raises).
`notify_serve_backends(name, home, records=None)` returns the per-backend list, and `notify_serve_backend(name, home)`
folds it. `_serve_backend_record()` is kept (returns the first record) for older `hermes-sync` callers.
`backend_label(record)` formats `<role>:<port>`.

## Code

`hermes_cli/plugins_activation.py`.

## Tests

Not rerun by this mirror pass.

- New `tests/hermes_cli/test_plugin_activation_serve_fanout.py` (real host records in a temporary rendezvous dir, real
  HTTP listeners): `test_both_live_records_get_the_activation`, `test_host_owner_only`, `test_desktop_only`,
  `test_no_record_answers_none`, `test_dead_host_owner_record_does_not_block_the_desktop_child`,
  `test_inconsistent_token_record_is_skipped_and_the_other_still_reached`,
  `test_unreachable_backend_is_named_and_the_other_still_activates`,
  `test_cli_hint_reports_a_failed_desktop_backend_even_when_the_host_owner_loaded_it` (`activate_plugin_now`, the CLI
  activation path).
- Updated: `test_plugin_activation_backend_failure.py` (stubs `serve_backend_records`), `test_web_server.py`
  (Desktop-only rendezvous isolation now reads `backends`), `test_dashboard_session_token_routes.py` (the AST guard now
  finds the session-header sender in `notify_serve_backend_record`).

Untested: a live Desktop app plus a supervised dashboard on one machine; the `hermes-sync` caller of
`_serve_backend_record`.

## Applying

Apply in series order. No config.

## Gotchas

- Callers that read only the top-level answer still see one backend's result; read `backends` for per-backend status.
- A backend that restarts after activation loads plugins from disk at its own start; activation does not retry.

## Plugin?

No. This is the core CLI-to-backend activation path that plugins themselves depend on.

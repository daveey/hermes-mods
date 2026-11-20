# Test isolation: host process discovery, psutil errors, range listener backlog, macOS boot lookup, wake baseline

Mirror snapshot 5011; ordinals 4360, 4364, 4365, 4366, 4372, 4443, 4449.

Entries:

- **4360**: isolate GC retention fixtures from host process discovery
- **4364**: Bound native test process errors and range listener backlog
- **4365**: Limit range backlog comment to demonstrated risk
- **4366**: Prove queued range connections remain usable on Darwin
- **4372**: test(kanban): isolate attachment cleanup from host process discovery
- **4443**: Test import-time wake baseline before first lease check
- **4449**: Isolate historical review fixtures from macOS boot lookup

## Behavior

- GC retention (4360) and attachment byte-custody (4372) fixtures supply an empty process inventory so the real owned-job veto cannot read host process state (deleted-cwd stats could trip the home I/O guard or leave scratch retained); an outer tripwire fixture makes omitting the isolation fail.
- Session cleanup (4364) recognizes only the specific psutil `proc_environ` AccessDenied-as-SystemError signature and preserves unknown errors; discovery is extracted to a sibling helper to respect the conftest size cap. The range test server reserves sixteen pending connections instead of five so eight concurrent clients do not hit SYN retries; the comment is limited to the demonstrated risk (4365) and each queued connection must stay usable on Darwin, where socket options and a byte echo failed with a backlog of five (4366).
- Wake baseline (4443): a fresh import, a fake 400-second suspend and the first lapsed-row lease check are exercised without manual tracker priming (retained grace, grace expiry and a no-suspend control).
- Historical review fixtures (4449) stub the unrelated macOS boot-time sysctl call that a gh-only subprocess fake intercepted during task claims, covering native and simulated Darwin with cold caches.

## How it works

Fixtures only; production identity is unchanged. The backlog value and psutil signature match live in `tests/pm/_range_server.py` and `tests/session_processes.py`.

## Code

These entries ship no non-test source paths; they change tests only.

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/conftest.py`
- `tests/hermes_cli/test_kanban_dependency_review_historical_cover.py`
- `tests/hermes_cli/test_kanban_gc_retention.py`
- `tests/hermes_cli/test_kanban_sync_attachments.py`
- `tests/hermes_state/test_wake_grace_import_baseline.py`
- `tests/pm/_range_server.py`
- `tests/pm/test_worker_observers.py`
- `tests/session_processes.py`
- `tests/test_session_daemon_reaper.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Whether the original flakes had the stated cause was reproduced via controlled boundaries, not proven against historical hosts.
- Darwin-specific behavior needs a Darwin runner to be meaningful.

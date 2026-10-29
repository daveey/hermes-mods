# Explicit per-board dispatch ownership

Patches: **0367 0368 0371 0372 0373 0374**.

## Behavior

A board can reserve dispatch for a dedicated runtime with an explicit `dispatcher_id`. Stock CLI, daemon and embedded gateway callers omit that identity and skip owned boards. Stock gateway auto-decomposition and stuck-dispatch health accounting also exclude owned/invalid boards. Missing ownership keeps stock eligibility; invalid or unreadable ownership fails closed.

Operator set/read/clear commands target the named board's canonical database, not a worker environment pin:

```sh
hermes kanban boards dispatch-owner example dedicated-worker --json
hermes kanban boards dispatch-owner example --json
hermes kanban boards dispatch-owner example --clear --json
```

This is cooperative same-UID coordination, **not a security boundary or credential**. Direct task APIs are unchanged. Do not give task workers dispatcher identities.

## How it works

Ownership authority is the adjacent `<database filename>.dispatch-owner.json` sidecar, not display `board.json`. The canonical SQLite main path selects both authority and dispatch lock. A native connection captures device/inode at open; dispatch and gateway pre-work revalidate under the lifecycle lock. Symlink aliases resolve to the same authority. Raw/in-memory handles and relocated stale connections cannot bypass ownership.

Ticks check ownership before reconciliation, reclaim, promotion, claim, spawn, checkpoint or observers. Metadata changes use atomic replacement under the same nonblocking lock. Lock acquisition failure refuses dispatch instead of falling back to an unguarded tick. An open made while busy can serve ordinary task APIs but must be closed/reopened before dispatch. Busy lifecycle operations report retryable errors; dashboard create/update returns HTTP 409.

Native archive moves a database and sidecar together; recreating the slug is a new unowned database. An external symlink target stays with its own adjacent sidecar. Export/import deliberately does not copy runtime ownership. Per-board gateway pre-dispatch failure is contained so other boards can continue.

| Patch | Role |
|---|---|
| 0367 | Explicit runtime identity and native dispatch gate; operator API/CLI. |
| 0368 | Stable database metadata identity and gateway decomposition gate. |
| 0371 | Isolate per-board pre-dispatch failures. |
| 0372 | Separate authority from display metadata and preserve lifecycle semantics. |
| 0373 | Reject relocated connections; capture/revalidate native database identity. |
| 0374 | Preserve lifecycle errors and report lock contention consistently. |

## Code

- `hermes_cli/kanban_dispatch_owner.py`: strict owner validation, canonical authority and locking.
- `kanban_db_connect.py`, `kanban_db_dispatch.py`, `kanban_db_boards.py`, `kanban_boards.py`, `kanban_transfer.py`: connection, tick and lifecycle integration.
- `hermes_cli/kanban.py`, `kanban_parser.py`, `kanban_ops.py`: operator surface.
- `gateway/kanban_watchers_dispatcher.py`: locked pre-work and per-board isolation.
- `plugins/kanban/dashboard/plugin_api.py`: lifecycle contention reporting.
- `website/docs/developer-guide/kanban-dispatch-ownership.md`: final source contract at `6385cf3622a002fc9ad7cd182b53f2e346358fa5`.

A dedicated runtime uses a native `connect_closing(board="example")` connection and calls `dispatch_once(conn, board="example", dispatcher_id="dedicated-worker", spawn_fn=scoped_spawn)`. It must supply its own authorized scoped spawn function, triage decomposition and health telemetry; no environment variable or implicit profile grants ownership.

## Tests

Source regression suites, **not executed in this documentation pass**:

- `tests/hermes_cli/test_kanban_dispatch_owner.py`: native dispatch denial/allowance and strict metadata.
- `test_kanban_dispatch_owner_operator.py`: operator set/read/clear and CLI behavior.
- `test_kanban_dispatch_owner_regressions.py`: gateway, alias/path and decomposition regressions.
- `test_kanban_dispatch_owner_lifecycle.py`: archive/recreate and authority separation.
- `test_kanban_dispatch_owner_connection.py`: native connection identity and relocation.
- `test_kanban_dispatch_owner_busy.py`: contention and retry behavior.
- `tests/plugins/test_kanban_board_lifecycle_api.py`: dashboard lifecycle errors.
- `tests/gateway/test_kanban_pre_dispatch_isolation.py`: one board's pre-work failure does not stop other boards.

Run these with `bash scripts/run_tests.sh <files>` in the isolated applied checkout. Executed full-series and selected test results are recorded in [mirror verification](mirror-377-verification.md); no live board ownership was set by this mirror.

## Applying

Apply all ownership follow-ups, not only 0367. Before activation, stop all older dispatchers that can reach the board and drain workers at a safe idle window; then upgrade every dispatcher. Mixed-version operation is unsupported because older code ignores ownership and may lock an unresolved alias. Metadata writes alone are not rollout. No automatic migration from earlier unreleased display-metadata storage is provided.

## Gotchas

- IDs are exact lowercase alphanumeric/hyphen/underscore slugs, 1–64 characters, starting alphanumeric. No trim/case-fold/coercion; present null/empty ownership is invalid, not cleared.
- Move the sidecar with its database. Hard-link aliases and independent relocation/bind mounts without the sidecar are unsupported.
- Manually replacing a database while retaining its sidecar retains ownership. Clear it explicitly if that is not intended.
- Auto-decomposition holds the lock through auxiliary calls; owner/display edits may be busy for that duration. Retry, never bypass the lock.
- Ownership does not terminate existing workers, start a dedicated service or authorize arbitrary same-UID writes.

## Plugin relationship

A dedicated runtime can use the native API with a scoped spawn function; no monkeypatch is needed. Core must enforce the shared lifecycle lock and connection identity, including stock dispatchers and dashboard lifecycle endpoints.

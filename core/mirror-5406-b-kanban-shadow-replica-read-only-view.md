# Kanban: shadow-mode board replica cards shown read-only in CLI list/show and the Desktop board

Mirror entries 5144, 5145, 5185.

## Behavior

- A board whose sync mode is `shadow` keeps only this gateway's own cards in the live database; the full board lives in a read-only replica. List/show previously showed only the local handful. Now, when sync is enabled and the replica exists, `hermes kanban list [--json]` appends replica rows (`[remote:<gateway>, read-only]`) and `show <id>` falls back to the replica for a card only it has (it has no queue fields; it waits on its home gateway). A live row wins an id collision.
- The dashboard `/board` payload merges replica cards, including the done-column total and omitted counts; writes on such cards keep answering 409 `foreign_card`.
- 5145: done history is counted across the replica before the cap, collisions are filtered before the final window, an unreadable replica leaves live rows readable, explicit CLI sorts apply across the combined list, and the Desktop board disables drag, bulk selection, file drops and card context mutations for read-only replicas.
- 5185: the in-memory sort scratch database gets the existing line-scoped exemption from the board-lock source scan.
- The replica is opened read-only and never written; the dispatcher never claims a shadow row.

## How it works

- `hermes_cli/kanban_shadow_view.py` (merge, marking, unknown home owner, `:memory:` sort), `hermes_cli/kanban.py`, new `hermes_cli/kanban_show_print.py` (print helpers moved out to stay under the file-size cap).
- `plugins/kanban/dashboard/board_shadow.py` and `plugin_api.py`: payload merge memoised on the replica file stat.
- Desktop: `apps/desktop/src/plugins/kanban/{api,board,types,i18n}.ts(x)`; `scripts/boundary_lint_baseline.json` records the boundary baseline.

## Code

- `hermes_cli/kanban_shadow_view.py`
- `hermes_cli/kanban_show_print.py`
- `hermes_cli/kanban.py`
- `plugins/kanban/dashboard/board_shadow.py`
- `plugins/kanban/dashboard/plugin_api.py`
- `apps/desktop/src/plugins/kanban/board.tsx`
- `apps/desktop/src/plugins/kanban/api.ts`

## Tests

Source tests (not executed in this mirror pass):

- `tests/hermes_cli/test_kanban_shadow_view.py`: merge marked remote/read-only, home gateway in text, filters, live row wins, replica-only show, other modes and sync-disabled do not merge, reading never writes, read-only connection, dispatcher never claims shadow row; 5145 adds unknown home, unreadable replica, combined sort, history counted before cap.
- `tests/plugins/test_kanban_board_shadow.py`: board payload marking, collision, done total, live-only without shadow, memo follows a replica commit, HTTP route and refused writes; 5145 adds unknown home/owner cache and live-connect failure.
- Desktop: `card-remote.test.tsx` and `api.gateways.test.ts`.
- 5185 relies on the existing lockguard source-scan test.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Shadow primary-mode partitioning is the reason the fix is read-side only; do not copy replica rows into the live file.
- Replica freshness is whatever sync last wrote.

## Why core instead of a plugin

The CLI, dashboard route and Desktop board are core surfaces that read the core sync replica format; a plugin could not change `kanban list` output or the built-in board.

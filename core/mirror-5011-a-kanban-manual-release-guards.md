# Kanban manual release guards: unread foreign comments and pending spend decisions

Mirror snapshot 5011; ordinals 4403, 4404, 4405, 4445, 4446.

Entries:

- **4403**: fix(kanban): refuse manual release over unread foreign comments
- **4404**: fix(kanban): resolve unread imported comments by portable identity
- **4405**: fix(kanban): require distinct comment coverage during partial sync
- **4445**: fix(kanban): refuse unblock while a spend decision is pending
- **4446**: fix(kanban): resolve delayed legacy comments from proven local runs

## Behavior

- **Unread comments (4403)**: manual promote/unblock previously consulted lifecycle state only, so a decision retracted after the caller read the thread could still be released. Under the status writer lock, an optional seen-comment watermark (default: last 60 minutes) is enforced at CLI and tool boundaries; foreign comment provenance is listed with a rerun command and the call exits 4 / `unread_comments` without mutating. Autonomous DB paths and owner-wake paths are unaffected.
- **Imported comments (4404, 4405, 4446)**: events are resolved by portable identity (`comment_uid`) to receiver-local ids; legacy synced events conservatively list same-time/same-author candidates and missing data fails closed until sync repairs it. During partial sync, every connected candidate component must have an injective event-to-row mapping (own, old and un-attributed events count), so two legacy events cannot resolve to one already-read comment. A delayed legacy event resolves only when its writer run is a run of the same task whose claim receipt names this board's sync gateway as holder, with matching row id and author; everything else keeps the strict window and refusal.
- **Spend decision (4445)**: unblock of a spend-held triage card used to report released and then be silently re-held. It is refused in the existing transaction-owned guard with shared promotion guidance (CLI, tool, operator and supervisor resume preflight), and `spend_budget_held` is recorded only when the pending branch actually flips status.

## How it works

`kanban_release_comments.py`, `kanban_db_comment_ids.py`, `kanban_cli_release.py`, `kanban_db_promote.py`, parser and tool schemas / self-heal; `kanban_spend_budget.py`, `kanban_spend_decision.py`, `kanban_resume_exec.py` and `kanban_terminal_guard.py` for the spend path.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_cli_release.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_comment_ids.py`
- `hermes_cli/kanban_db_promote.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_release_comments.py`
- `hermes_cli/kanban_resume_exec.py`
- `hermes_cli/kanban_spend_budget.py`
- `hermes_cli/kanban_spend_decision.py`
- `hermes_cli/kanban_terminal_guard.py`
- `tools/kanban_tools_schemas.py`
- `tools/kanban_tools_self_heal.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/hermes_cli/test_kanban_release_imported_comments.py`
- `tests/hermes_cli/test_kanban_release_unread_comments.py`
- `tests/hermes_cli/test_kanban_spend_unblock.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Using the seen override is an explicit operator act; nothing silently releases.
- Null event origin or a `local-` session prefix alone is not host provenance, since imports can carry both.

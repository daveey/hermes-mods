# Gateway-aware comment waits and stale-probe recovery

## Behavior

Patches **1119, 1120** add nullable `task_comments.origin_gateway`, a `wait-comment` CLI probe, and refusal of author-filtering comment wake checks. A linked hub request whose owner has completed can wake a parked card once when its old probe still reports not-yet.

## How it works

`add_comment(..., origin_gateway=...)` chooses an explicit validated gateway, an unambiguous relay/card author spelling, or the local gateway. `kanban comment --origin-gateway` exposes the explicit value. Migration backfills recognized `hub:gw:<gateway>` and `<gateway>/t_...` authors; ambiguous historical authors remain null. The column is machine-local in `kanban_db_sync.LOCAL_COLUMNS`, not a globally synchronized authority.

For example, with generic task and gateway placeholders:

```sh
hermes kanban wait-comment <task> --after-id <last-id> --from-gateway worker-a --contains verified
```

The CLI exits 0 for a match, 1 for no matching comment yet, and 2 for a missing task or caught SQLite error. Matching requires `id > after_id`; gateway matching is case-insensitive equality on `origin_gateway` **or substring matching on author**, and body matching is case-insensitive substring matching. This is a wake convenience, not authenticated sender verification.

`kanban_awaiting.resolve` calls `check_park`, which rejects recognizable `task_comments` author predicates with `author_probe` and suggests `wait-comment`. `author != ...` remains permitted. The opt-out is `kanban.author_probe_guard: false`; unreadable config keeps the guard enabled.

When a wake probe exits 1 without an output error, `answered_request` searches request tokens in the park/probe against the hub's completed requests and checks owner completion. A request already recorded in a `wake_probe_stale` event is skipped. `apply_result` uses the existing generation/state fence, wakes the card, and appends a dispatcher comment; wake context and diagnostic readiness floors recognize the new event.

Patch 1120 extracts `own_comment_ids`, `delivered_comment_ids`, and `comment_id_before_run_start` into `kanban_db_comment_ids.py`, re-exporting them from `kanban_db`; migration moves into `ensure_origin_column`. It also narrows selected error catches while retaining conservative config-error handling.

## Code

- `hermes_cli/kanban_comment_origin.py`: origin resolution/backfill, `find_comment`, `run_wait_comment`, `check_park`, `answered_request`.
- `hermes_cli/kanban_parser.py`, `hermes_cli/kanban.py`: command/option dispatch.
- `hermes_cli/kanban_db.py`, `kanban_db_connect.py`, `kanban_db_sync.py`: persistence/migration/local-column rule.
- `hermes_cli/kanban_wake_check.py`: `_probe_and_apply`, `apply_result`, `_render_wake_context`.
- `hermes_cli/kanban_db_comment_ids.py`: extracted bridge queries (1120).
- `tools/kanban_tools_schemas.py`: wake-check guidance.

## Tests

Tests identified from source, **not executed in this documentation pass**. Patch 1119 adds `tests/hermes_cli/test_kanban_comment_origin.py`:

- `test_park_on_an_author_probe_is_refused` parameterizes tool schedule/block/continue; `test_author_like_and_in_probes_are_refused_on_the_cli` exercises CLI schedule.
- `test_author_not_equal_own_notes_still_parks`, `test_wait_comment_probe_parks`, `test_guard_can_be_switched_off` check accepted/opt-out cases.
- `test_wait_comment_matches_any_author_spelling_of_the_gateway`, `test_wait_comment_after_id_and_contains` exercise CLI matching, watermark, body filter, and missing-task return code.
- `test_comment_cli_stores_origin_gateway`, `test_migration_backfills_origin_gateway` check CLI storage and legacy migration.
- `test_answered_hub_request_wakes_a_card_whose_probe_still_fails` checks dispatcher wake, comment, and once-only behavior; `test_pending_hub_request_keeps_the_card_parked` checks an incomplete owner.

Patch 1120 adds no tests. Its query extraction, narrowed exceptions, and re-export compatibility were inspected from the diff, not independently executed. No replica-sync integration test is added in these patches.

## Applying

These are ordered full-fork series patches: replay 1119 then 1120 with preceding patches from the documented base (`git am -3 --empty=keep`). They rely on existing awaiting, wake-generation, capability-request, and board migration infrastructure. No standalone application test was performed here.

## Gotchas

Save the relevant last comment ID when parking; zero also matches old replies. Author substring fallback can match broader names than intended. The guard is textual pattern detection, not a SQL parser. Stale-probe detection applies only to recognized completed hub request/owner evidence, not arbitrary external waits, and deliberately skips capability-owner probes. A wake means inspect the answer, not that the deliverable has been verified.

## Plugin

No plugin is added. A relay can provide the sender through the core comment API/CLI. Persistence, park validation, and dispatcher wake handling require core support.

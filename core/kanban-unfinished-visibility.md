# Unfinished Kanban visibility and conditional holds

Patch: `0284`. Complements [sidebar-status-stripe.md](sidebar-status-stripe.md).

## Behavior

- Both Desktop activity-dot paths treat blocked-only Kanban work as unfinished, including after a goal finishes. It must not produce a green Finished dot. The stripe already prioritizes blocked/open work.
- A cross-process archive write invalidates the activity-count cache immediately on its next read instead of retaining archived task counts for the three-second TTL.
- The `kanban_create` tool rejects `initial_status=blocked` with unfinished explicit parents unless `human_action_now=true`. Use parent dependencies for future work, or scheduled time with a wake check for a readable external condition. A genuine immediate human action may still block now; a later approval is not automatically approved or rewritten.

## How it works

The Desktop store folds blocked counts into both the stored-session dot and row-activity fallback. Backend cache identity includes database and WAL path, inode, size and nanosecond modification time. The creation guard examines actual unfinished parent records, not prose heuristics.

## Code

- `apps/desktop/src/store/session-dot-state.ts`
- `hermes_cli/kanban_session_activity.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_schemas.py`

## Tests

From the source root at core commit `39cdabdc62` (or the complete applied public series), run these as separate invocations:

```sh
bash scripts/run_tests.sh tests/hermes_cli/test_kanban_session_activity.py -q -j1
bash scripts/run_tests.sh -p hermes_bootstrap tests/tools/test_kanban_tools.py -q -j1
```

Verified separately on exact core commit `39cdabdc62`: 22 activity tests passed and 64 tool tests passed. The tool suite needs early bootstrap imports to keep dependency provisioning outside the tests' real-home I/O guard. Do not combine these commands: the activity suite imports the CLI, which interprets pytest's `-p hermes_bootstrap` as a profile selection and fails with `Profile 'hermes_bootstrap' does not exist`.

The implementation owner's handoff additionally reports 87 passing Desktop state/row/archive-guard tests, including blocked-only dots and completed goals, plus TypeScript and ESLint checks. Those Desktop tests were not independently rerun for this mirror. Backend regression exercises archive cache invalidation; tool tests exercise rejection, deferred approval, and explicit immediate human action.

## Applying

Apply the complete ordered `core/patches/` series with `git am -3` at its upstream/fork merge-base, not an independently advanced upstream tip. All 284 patches were successfully applied at `4957bbd0a0` for this export.

## Gotchas

- Cache invalidation explains sub-three-second staleness, not a proven minutes-long discrepancy.
- The creation guard covers the tool with explicit parent IDs; CLI, REST and unstructured textual conditions are not claimed to be guarded.
- No live renderer deployment or historical screenshot reproduction is claimed by this mirror.
- Existing approvals, task statuses and continuation budgets are not automatically rewritten.

## Why core rather than a plugin?

Activity counts, both Desktop dot resolvers and the canonical task-creation tool own these contracts. A plugin-only overlay would leave sibling paths inconsistent.

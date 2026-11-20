# Pool-ahead wait notices and capable dispatcher verdicts

Patches: **4564, 4637–4638** (5011 snapshot).

## Behavior

Machine-lane wait notices and `kanban show` queue notes can report how many ready unclaimed predecessors matching workers would also run, not only cards whose labels equal the waiting card. Label-less predecessors are omitted. Without machine labels, output is unchanged.

When machine placement omits a skip, pool wait notices still carry the newest capable ready-episode dispatcher verdict (pacing or quota wall). Busy/update precedence is preserved. Advisory governor reads stay at the notice's audited read-only boundary.

## How it works

`kanban_label_backlog.read()` accepts optional `machine_labels`. When given, `pool_ahead` is computed in the existing ready-row loop. `placement()` passes labels from the carried matching-machine map. `words()` prepends the pool-ahead clause only when that key is present. `move_refusal()` still calls `read()` without it.

Notice verdict carriage uses governor pools or recorded providers and renders `llm_paced`. A failed governor read keeps a recorded pacing verdict. Recorder-only newer skips do not beat a capable verdict.

## Code

- `hermes_cli/kanban_label_backlog.py`
- `hermes_cli/kanban_queue_position.py`
- `hermes_cli/kanban_llm_pacing_dispatch.py`
- `hermes_cli/kanban_notice_verdict.py`
- `hermes_cli/kanban_parent_unclaimed.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_label_backlog_pool_ahead.py`
- `tests/hermes_cli/test_kanban_notice_pacing_admission.py`
- `tests/hermes_cli/test_kanban_notice_recorded_verdict.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

`pool_ahead` is approximate: label-less ready cards are not counted. Dispatch `backlog=False` is untouched. Do not replace a queue-provided skip with a carried verdict.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.

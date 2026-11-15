# Pacing history, quota consumers and stall explanations

Patches: **4263–4264, 4274–4276, 4287, 4289, 4344** (4352 snapshot).

## Behavior

Pacing skip history records an untimed hold again when it replaces an expired timed row, without unbounded flap duplication. A successful account usage read with both windows null counts as fresh headroom; failed reads do not.

A never-imported pool card's stall notice can explain a positively known pacing wall instead of suggesting needless replacement. Credential and quota-consumer regressions ship alongside this behavior.

## How it works

Dispatcher history persists across process restarts and usage reads obey the configured read interval. A renamed empty-window regression suite gives the behavior a stable name.

Notice admission reads only recent dispatcher-stored pace and applies the governor's start rules. All candidate pools must positively hold the card. It neither reads external usage nor writes governor state. Real execution-capable dispatcher verdicts, busy pools, update holds, missing shared-board records and held/draining matching machines retain precedence.

Additional tests pin rotation exclusion before resync/probes, subscription funding and stickiness, model-specific bench avoidance, earliest usable quota deadlines and episode reset recovery.

## Code

- `hermes_cli/kanban_dispatch_skip.py`
- `hermes_cli/kanban_llm_pacing_dispatch.py`
- `hermes_cli/kanban_parent_unclaimed.py`
- `website/docs/user-guide/features/kanban.md`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/agent/test_class_credential_reselection.py`
- `tests/agent/test_codex_rotation_window.py`
- `tests/hermes_cli/test_kanban_llm_pacing.py`
- `tests/hermes_cli/test_kanban_llm_pacing_empty_windows.py`
- `tests/hermes_cli/test_kanban_llm_pacing_persistence.py`
- `tests/hermes_cli/test_kanban_llm_pacing_review_3058.py`
- `tests/hermes_cli/test_kanban_notice_pacing_admission.py`
- `tests/hermes_cli/test_kanban_quota_release_consumers.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

No fresh usage data means no new pacing explanation. A local recorder's labels mismatch alone is not a fleet-wide admission verdict. Test-only credential entries do not change funding policy. No live provider quota calls were made.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.

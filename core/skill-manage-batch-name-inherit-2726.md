# skill_manage batch: a nameless op inherits the one skill its siblings name

Patches: **2715**.

## Behavior

- A `skill_manage` batch (`operations=[...]`) used to refuse any op without `name` (`operations[<n>] needs a 'name' (the skill it targets).`), costing a full resend when a model wrote `name` on a `patch` but not on the `write_file` for the reference it links (or the reverse).
- Now, if every named op in the call names the same single skill, a nameless op (missing, empty or non-string `name`) targets that skill. Its result row carries `name_inherited: true`. Fully named batches and batches using the legacy top-level `name` are unchanged; the top-level name still wins and is not flagged.
- Several different named skills, or no name anywhere: still refused before anything is written. The error now lists the targets (for example `other, probe`) and says to resend with `name` on every op.
- When a batch is staged for approval, the staged payload stores the resolved name on every op, so the approver's per-op diff shows the real target instead of no skill.

## How it works

`tools/skill_manager_batch.py`: `_validate_batch_ops` collects the sorted set of non-empty string names across the ops. If it has exactly one member, that is `sole_name`, and each op's target is `op.name or default_name or sole_name`. The refusal messages are extended as above. `_skill_manage_batch` builds the staging payload with `name` filled in from the resolved `names[i]`, and adds `name_inherited` to the result row when the op had no name of its own and no top-level default applied.

## Code

`tools/skill_manager_batch.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tools/test_skill_batch_name_inherit.py` (9 tests). Entry path: `tools.skill_manager_tool.skill_manage(operations=[...])` against a temp skills directory in YOLO mode (both nameless positions, refusal with several skills and nothing written, no name anywhere, non-string and empty names, top-level name precedence, fully named rows unchanged), plus the staged payload and `skill_pending_diff` / `apply_skill_pending` replay with the write gate stubbed. The real approval UI is **untested**. The commit message states that 5 of the new tests fail without the source change; that was not reproduced here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Inheritance is deliberately not a guess among several skills: one named op alongside ops naming a different skill still fails the whole batch.
- A batch whose only name is on one op targets that skill for every nameless op, so a typo in the single name sends all ops there.

## Plugin

Core change; no plugin required.

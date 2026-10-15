# Skill review fork counts a replayed, still-current skill_view as a read

Patches:

- `patches/0055-fix-skills-review-fork-counts-a-replayed-still-curre.patch`: skills. Before the background review
  fork runs, skill_view results in the history it replays count as reads when they still match the file on disk.

What you see: background skill reviews patch a skill the session already loaded on the first try. Before, the
review fork's first patch was refused with "content has not been loaded in this review turn", so it re-viewed the
skill first. For a large SKILL.md (tens of KB) that was a full reload on every review.

## Behavior

- The review fork replays the parent transcript. A `skill_view` result in it counts as a read of that file when it is
  byte-identical to what `skill_view` serves now, from the same source path.
- Only the newest copy of each `(skill, file)` counts. An older copy may predate a patch.
- **The read-before-write guard still fires for:** stale copies (the file changed since), dedup stubs
  (`content_returned: False`), failed views, binary files, pruned or summarised tool output (not JSON), output of
  other tools that happens to contain the same text, and a copy whose recorded source path now resolves to a different
  file (a shadowing skill appeared).
- A seeded supporting file (e.g. `references/workflow.md`) marks only that file, not the skill's SKILL.md.
- When the fork is routed to another model it replays a digest. Seeding uses exactly the history the fork sees, so a
  digest that dropped the view seeds nothing.

## How it works

- `tools/skill_manager_guards.py`:
  - `_transcript_skill_views(messages)` collects `(name, file, content, _source_path)` from every successful, text
    `skill_view` tool result. It finds the tool name from `tool_name`/`name` on the message or from the assistant
    `tool_calls` entry the result answers.
  - `seed_background_review_reads_from_history(messages, session_id=None)` walks those newest first. For each, it
    calls `_current_skill_view` and adds the resolved path to the background review read marks when content and
    source match. It returns the number of files marked. Errors are logged at DEBUG and skipped.
  - `_current_skill_view(name, file_path, session_id)` calls `tools.skills_tool.skill_view` inside
    `contextvars.copy_context()` with fresh marks, so `skill_view`'s own read-mark side effect does not leak into the
    fork's marks.
- `agent/background_review.py`, `_run_review_fork`: computes `review_history` once (digest if routed, else the full
  snapshot), calls `_reset_background_review_read_marks()` then the seeder with `agent.session_id`, and passes the
  same `review_history` to `run_conversation`. The seeding is inside `suppress(Exception)`, so a failure leaves the
  old behavior.

## Tests

- `tests/tools/test_skill_manager_tool.py` (in `TestCuratorConsolidationDeleteGuard`):
  `test_review_patch_allowed_when_replayed_view_is_current`,
  `test_review_patch_still_refused_when_replayed_view_is_stale`,
  `test_review_seeding_ignores_stubs_failures_and_other_tools`,
  `test_review_seeding_supporting_file_marks_only_that_file`.
- `tests/agent/test_background_review_memory_scope.py`:
  `test_fork_seeds_read_marks_from_the_history_it_replays`, parametrized over routed and not routed. It checks the
  seeder and `run_conversation` get the same history.

## Applying

Standalone. It builds on the upstream read-before-write guard for review forks (`_BackgroundReviewReadMarks`,
`_reset_background_review_read_marks`) and on `skill_view` returning `_source_path`.

## Gotchas

- Seeding runs one `skill_view` per distinct replayed view at fork start. That is a local file read, not a model
  call, but it is not free for a transcript with many views.
- The match is exact bytes. Any change to the file, or to how `skill_view` renders it, makes the replayed copy count
  as stale and the fork re-views as before.

## Plugin?

No. The read marks are a private context variable in `tools/skill_manager_guards.py` and the fork setup in
`_run_review_fork` has no hook.

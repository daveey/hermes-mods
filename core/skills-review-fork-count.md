# Skill review fork counts a replayed, still-current skill_view as a read

Patches:

- `patches/0055-fix-skills-review-fork-counts-a-replayed-still-curre.patch`: skills. Before the background review
  fork runs, skill_view results in the history it replays count as reads when they still match the file on disk.
- `0097`: name stale replayed skill copies in the review prompt so the fork reloads them before writing.
- `0128`: a read-before-write refusal serves the file's current text and counts as the read, so the fork's next
  call can be the corrected write.

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

- A stale replayed copy now produces a prompt note naming the skill and `SKILL.md` or supporting-file path, with instructions to call `skill_view` before writing. The guard itself is unchanged. Deleted files, failed/pruned views, and current copies do not produce this stale-copy note.

- Patch `0128`: when the guard still refuses (the fork never loaded the file), the refusal carries
  `current_content` (the file's current text, up to 120,000 chars) and marks the file read. The write itself is
  still refused, and the message tells the fork to rebuild the edit against `current_content` and resend it now
  (every operation, for a batch). Larger files get the old "call skill_view" refusal. Before, recovery took
  refuse → `skill_view` → retry, and review forks often stopped after the re-view with nothing saved.

## How it works

- `tools/skill_manager_guards.py`:
  - `_transcript_skill_views(messages)` collects `(name, file, content, _source_path)` from every successful, text
    `skill_view` tool result. It finds the tool name from `tool_name`/`name` on the message or from the assistant
    `tool_calls` entry the result answers.
  - `seed_background_review_reads_from_history(messages, session_id=None, stale=None)` walks those newest first. For each, it
    calls `_current_skill_view` and adds the resolved path to the background review read marks when content and
    source match. It returns the number of files marked. Errors are logged at DEBUG and skipped.
    With `stale=[]`, content or source-path mismatches append `(name, file_path)` to that list.
    `stale_skill_views_note` deduplicates and sorts labels for the review prompt.
  - `_current_skill_view(name, file_path, session_id)` calls `tools.skills_tool.skill_view` inside
    `contextvars.copy_context()` with fresh marks, so `skill_view`'s own read-mark side effect does not leak into the
    fork's marks.
- `agent/background_review.py`, `_run_review_fork`: computes `review_history` once (digest if routed, else the full
  snapshot), calls `_reset_background_review_read_marks()` then the seeder with `agent.session_id`, and passes the
  same `review_history` to `run_conversation`. The seeding is inside `suppress(Exception)`, so a failure leaves the
  old behavior.

- Patch `0128`, `tools/skill_manager_guards.py` `_background_review_read_before_write_guard`: reads the target
  (`utf-8-sig`, errors suppressed); at or under `_INLINE_REFUSAL_MAX_CHARS` it calls
  `mark_background_review_skill_read(target)` and returns `_refusal(..., _read_before_write_required=True,
  current_file=..., current_content=...)`.

## Code

Implementation: `tools/skill_manager_guards.py` and `agent/background_review.py`.
Regression tests: `tests/tools/test_skill_manager_tool.py` and
`tests/agent/test_background_review_memory_scope.py`.

## Tests

- `tests/tools/test_skill_manager_tool.py` (in `TestCuratorConsolidationDeleteGuard`):
  `test_review_patch_allowed_when_replayed_view_is_current`,
  `test_review_patch_still_refused_when_replayed_view_is_stale`,
  `test_review_seeding_ignores_stubs_failures_and_other_tools`,
  `test_review_seeding_supporting_file_marks_only_that_file`.
- `tests/agent/test_background_review_memory_scope.py`:
  `test_fork_seeds_read_marks_from_the_history_it_replays`, parametrized over routed and not routed. It checks the
  seeder and `run_conversation` get the same history.

Patch `0097` adds `test_fork_prompt_names_skills_whose_replayed_copy_is_stale`,
checks the stale list in the existing refused-write test, and adds
`test_review_seeding_reports_no_stale_for_current_or_unviewable_copies`.

```sh
scripts/run_tests.sh tests/tools/test_skill_manager_tool.py tests/agent/test_background_review_memory_scope.py
```

Both files passed in the full-series regression run (October 16, 2026 PT).

Patch `0128` adds, in `TestCuratorConsolidationDeleteGuard`,
`test_background_review_refusal_serves_current_content_so_retry_lands` (single-op and batched: the very next
corrected write lands with no `skill_view` in between) and
`test_background_review_refusal_falls_back_when_file_too_large_to_inline`. `tests/tools/test_skill_manager_tool.py`
(83 tests) passes on the full 0001–0129 series (October 18, 2026 PT).

## Applying

Apply the ordered `0001–0097` prefix (or the complete `0001–0105` series) from this
mirror to the fork's documented merge-base in a disposable checkout. This feature
spans `0055`, `0097` and `0128` (apply `0001–0128` for all three); isolated application is not claimed. It relies on the
review read-before-write guard and `skill_view` source-path metadata. Run tests
from the resulting Hermes source root.

## Gotchas

- Seeding runs one `skill_view` per distinct replayed view at fork start. That is a local file read, not a model
  call, but it is not free for a transcript with many views.
- The match is exact bytes. Any change to the file, or to how `skill_view` renders it, makes the replayed copy count
  as stale. Patch `0097` now asks the fork to re-view before its first write, instead of relying on a failed write to prompt the reload.
- Prompt guidance does not grant read marks: stale copies remain unmarked until actually reloaded, and digest histories can only identify views they retained.

## Plugin?

No. The read marks are a private context variable in `tools/skill_manager_guards.py` and the fork setup in
`_run_review_fork` has no hook.

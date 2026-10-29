# Skill write size pressure and no-change feedback

Patches: `0102` (size feedback), `0103` (no-change success), `0116` (stale-patch recovery).

## Behavior

Successful `SKILL.md` creates, full rewrites, and default-target patches that grow
the file to at least half its 100,000-character hard cap return `size_warning`.
Every growing write at that level warns, not merely the first threshold crossing.
The warning names the largest level-two sections and gives a split recipe.
Over-cap errors retain their existing prefix, add the overage, and explain how to
free room before growing the file.

A patch that makes no change can now succeed with `no_change: true` and a `note`:
identical nonblank old/new strings present in the file, or a recognized already-
applied edit whose replacement is present and old text is gone. No file write is
performed. Such an operation no longer aborts an otherwise valid batch, and
background-review summaries do not announce it as a patch.

Failed patches return a structured `recovery` call to `skill_view` for the exact
skill/file, with instructions to use a fresh unique anchor and preserve newer
content. Batch failures expose `rollback_complete`; successful rollback reports
`operations_applied: 0` and instructs retrying the entire corrected batch. Failed
rollback instead requires inspecting all affected files before retrying. Matching
thresholds are unchanged: this improves recovery, not automatic stale-edit merging.

## How it works

`_attach_size_warning` compares before/after lengths against
`_SKILL_SIZE_WARN_FRACTION = 0.5`. `_section_sizes` measures `## ` sections while
ignoring headings inside fenced code; `_largest_sections_note` lists up to three
largest sections. The recipe is one ordered `skill_manage` batch:

1. `write_file` the extracted text to `references/<topic>.md`.
2. Patch the original section down to a pointer.
3. Apply the growing edit.

`_validate_content_size` adds this guidance to `SKILL.md` cap errors.
On a fuzzy-match error, `_patch_skill` tries `_already_applied_patch`, using
`tools.fuzzy_match.is_already_applied` for previously applied edits. The batch
advisory fields propagate `size_warning`, `no_change`, and `note` to each result
row. `_action_lines` skips no-change single results and batch rows.

## Code

- `tools/skill_manager_tool.py`: size helpers, create/edit/patch integration, and
  `_already_applied_patch`.
- `tools/skill_manager_batch.py`: `_ADVISORY_KEYS` propagation.
- `agent/background_review.py`: `_action_lines` summary filtering.
- `tests/tools/test_skill_size_limits.py`: size-pressure regressions.
- `tests/tools/test_skill_patch_no_change.py`: no-op, repeat-edit, and summary tests.
- `tests/tools/test_skill_manager_tool.py`: absent identical-anchor failure case.

## Tests

```sh
scripts/run_tests.sh tests/tools/test_skill_size_limits.py tests/tools/test_skill_patch_no_change.py tests/tools/test_skill_manager_tool.py
```

The size tests cover repeated growth warnings, quiet shrink/small writes, batch
advisories, section-aware cap errors, shrink-before-grow success, and fenced
headings. No-change tests cover present identical anchors, a continuing batch,
repeated applied edits, absent-anchor failure, and suppressed review summaries.
All three files passed in the full-series regression run (October 16, 2026 PT).

Patch 0116 regression coverage: `tests/tools/test_skill_patch_recovery.py` tests
both stale integration-list anchors against default/supporting-file targets,
rollback followed by whole-batch retry, ambiguous anchors, and failed rollback.
All six tests passed, together with 97 existing skill-manager, batch, and no-change
tests (103 total). Targeted Ruff passed; the full `scripts/check` command timed out
without output. These are registry-dispatch tests, not a live model recovery test.

## Applying

Apply the ordered `0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103` prefix (or the complete `0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105` series) from this
mirror to the fork's documented merge-base in a disposable checkout. Both `0102`
and `0103` change the skill manager and batch result fields; isolated application
is not claimed. Run the test command from the resulting Hermes source root.

## Gotchas

- Size warnings are advisory, separate from the linter's soft-budget warning.
  Shrinking/equal-size writes do not emit this growth warning. Supporting-file
  patches do not use the `SKILL.md` growth-warning path.
- The hard cap still rejects growth over the limit. Skill batch operations apply
  in order: shrinking must precede growth, unlike memory's final-result budget
  check. A real failure still triggers the normal batch rollback.
- A no-change result is not evidence of a new write; batch `operations_applied`
  can include successful no-op operations. Inspect `no_change` on result rows.
- Identical text absent from the file still fails. Existing lookup, path, and
  write guards are not bypassed by this fallback.

## Plugin?

No. These changes alter core skill mutation validation, atomic-batch results,
and review summaries. A plugin's separate tool would not fix `skill_manage`.

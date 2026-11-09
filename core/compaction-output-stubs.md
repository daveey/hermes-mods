# Compaction output stubs and missing-image sibling hints

Patch **2065**.

## Behavior

- `terminal` and `execute_code` summaries count decoded output lines rather than newlines in the JSON envelope. Nonempty lossy stubs retain first/last nonblank output lines and end with `(output elided: re-run or list the dir to see it)`.
- During ordinary pruning, either tool's nonempty output stays verbatim when it has at most **12 lines** and at most **600 characters summed across lines**. Pressure demotion can override this protection.
- A missing local-backend image path can name up to **10 real sibling files** sharing its non-digit stem prefix and case-insensitive suffix, instead of inviting filename guesses.

## How it works

The new output-stub module decodes the JSON `output` string, falls back to raw text for invalid/non-object/empty-object input, and uses `splitlines`. A nonempty JSON object without string output yields no output lines. Edge lines are stripped and clipped individually to **80 characters**, including an ellipsis when clipped; only nonblank edges appear. Terminal keeps a bounded command preview and exit code; execute_code keeps a bounded code preview. The character count in a stub describes the original result envelope, not just decoded output. Stub formatting retains the existing recognition marker to avoid summarizing a stub again.

`_demote_tool_result_at` enables short-output protection when `protected_skills` is not None; the pressure path passes None. The threshold sums line lengths, excluding newline separators, and protects the original complete tool result.

`_sibling_hint` scans at most 5,000 directory entries, matches the prefix before the first digit in the requested stem and its suffix, natural-sorts matches, and shows ten with a `(+N more)` suffix when needed. Unreadable/missing directories or no matches keep the plain error.

## Code

- `agent/context_compressor_output_stubs.py`: decoding, thresholds, edge formatting and both tool summarizers.
- `agent/context_compressor.py`: summarizer wiring and ordinary-prune protection.
- `tools/image_source.py`: local miss hint in `resolve_image_source`.

## Tests

On the README-replayed public tree, `scripts/run_tests.sh -j 1 tests/agent/test_tool_result_summaries.py tests/tools/test_image_source_not_found.py` returned **9 passed, 0 failed** (6 summary tests and 3 image-source tests). The test environment was independently provisioned with `pm.build_env --group dev --group test`; no source-tree runtime test comparison is claimed.

- `tests/agent/test_tool_result_summaries.py::test_terminal_stub_counts_decoded_output_lines_and_keeps_first_and_last`: terminal decoded count, both edges, elision and stub recognition.
- `tests/agent/test_tool_result_summaries.py::test_execute_code_stub_counts_decoded_lines_and_says_output_was_elided`: execute_code decoded count, both edges and elision.
- The same file tests non-JSON terminal fallback, short **terminal** ordinary-prune preservation, long terminal demotion and missing output fields. **Execute_code ordinary-prune preservation, pressure override and exact threshold/80-character clipping boundaries are not separately tested in this patch.**
- `tests/tools/test_image_source_not_found.py` calls `resolve_image_source` with the local backend: matching siblings, ten-entry cap/more count, and missing-directory plain error. It does not execute the outer `vision_analyze` tool or container fallback.

## Applying

Apply 2065 through [the core replay instructions](README.md), retaining earlier compressor pruning and image resolver infrastructure. No configuration knob is added.

## Gotchas

An elided listing is not a complete file inventory: re-run the command or list the directory before choosing another path. The actual edge limit is 80 characters **per line**, not 180. Short-output preservation is conditional and not permanent under pressure. Blank output has no elision note. The sibling scan is bounded, so `(+N more)` counts matches in the scanned subset, not necessarily the full directory; container/remote misses do not gain this hint.

## Plugin eligibility

Not plugin-only: compaction mutates retained tool history in core, and the image resolver emits its own source errors. A tool wrapper would not cover both boundaries.

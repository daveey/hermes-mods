# Standing instructions survive compaction and hold a don't-wait block

Patch: 0669 (core `9671590432`).

## Behavior

A user's lasting directive ("don't block, don't wait") is kept word for word. It is not left to
the summarizer, which once paraphrased a relayed "dont block, dont wait" into the opposite.

- Every compaction summary, lean and legacy, ends with `## Standing instructions (still in force)`
  and one `standing: <words>` line per directive. A later compaction re-reads those lines, so they
  survive a second summary.
- `build_worker_context` prints the same section up front, from the card body and from every
  comment, including comments the tail-capped comment thread would drop.
- `kanban_block` with `kind=needs_input` or a `human_request`, on a card that carries a standing
  don't-wait, is refused once. The refusal quotes the words and tells the worker to decide and
  continue. A `standing_wait_hold` event is recorded. A second call with the same block lands it,
  so a genuine human-only need (a credential, a physical step) is not trapped.

Two shapes count as standing: an explicit `standing: <words>` line anywhere, and a don't-wait
directive (don't/do not/never block, wait, pause, ask, or stop; or "no check-ins") inside a real
or relayed user message, taken as the quote or sentence that holds it. Assistant prose does not count.

## How it works

`agent/standing_instructions.py` is pure text. `explicit_lines` matches `standing:` lines.
`directive_spans` matches the don't-wait class only in user-authored text. `dedupe` keeps first-seen
order, case-insensitive, newest 20, 400 characters each.

The compressor's `_append_standing_section` collects explicit lines from the summary itself (so a
previous section is re-read) and extracts from each compacted turn. A section the summarizer copied
is stripped and replaced, never stacked. The rendered section is redacted with the usual compaction
redactor before it is appended. Legacy tail mode gets this section and nothing else from the lean
augmenter.

`_ctx_standing` runs the same explicit-line extractor on the body and every comment, before the
tail-capped comment list. The block guard uses `says_dont_wait` on those lines. It holds only
`needs_input` or a block that carries `human_request`. A prior `standing_wait_hold` on this run
lets the second call through.

## Code

- `agent/standing_instructions.py`: extractor, renderer, `says_dont_wait`
- `agent/context_compressor.py`: `_append_standing_section`, called from `_augment_summary_lean` for every mode
- `hermes_cli/kanban_db.py`: `_ctx_standing` in `build_worker_context`
- `tools/kanban_tools.py`: `_standing_wait_guard` in the `kanban_block` handler

## Tests

`tests/agent/test_standing_instructions.py` (5 tests):

- `test_relayed_standing_words_survive_two_compactions_verbatim`: a relayed user message's words
  are a `standing:` line after one compaction and still present, marked ACTIVE, after a second.
- `test_legacy_tail_mode_also_carries_standing_lines`: legacy mode still appends the section.
- `test_assistant_prose_is_not_a_standing_instruction`: the same words in an assistant row are dropped.
- `test_worker_context_always_shows_standing_comments`: a `standing:` comment renders ahead of the
  comment thread.
- `test_needs_input_block_under_standing_dont_wait_is_held_once_with_hint`: the first
  `kanban_block` returns an error containing the standing words and "decide", the card stays
  `running`, a `standing_wait_hold` event is logged, and the second call returns no error.

Untested: a don't-wait directive that is not on a `standing:` line inside a card body (the block
guard and worker context read explicit `standing:` lines only; the compressor also extracts
directives from user messages). A block whose kind is not `needs_input` and that has no
`human_request` is not covered. The CLI `hermes kanban block` path is not covered (the test calls
the tool handler).

## Applying

`git am -3 core/patches/0669-*.patch` after 0001–0668.
`scripts/run_tests.sh tests/agent/test_standing_instructions.py`.

## Gotchas

- The hold is once per run (`standing_wait_hold` with this run id, or any such event when the
  caller has no run id). A later run holds again.
- Only explicit `standing:` lines reach workers and the block guard. A don't-wait sentence that
  was never written as `standing:` still survives compaction, but a worker will not see it unless
  the summary's `standing:` line is copied onto the card.
- The guard quotes at most three lines and stores a 300-character reason on the event.

## plugin?

No. Compaction, worker context and `kanban_block` are core paths. A plugin cannot keep the words
verbatim across a summary or refuse the block before it lands.

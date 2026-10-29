# Preserve Kanban block-loop escalations for human input

Patches: `0100`, `0146`.

## Behavior

The dispatcher's automatic decomposition sweep leaves a triage card alone after
`block_loop_detected` until a later comment, edit, or status event occurs. This
prevents auto-decomposition from immediately re-promoting the same blocked work.
Ordinary triage cards remain eligible. Explicit specification or decomposition of
one card is unchanged.

**0146 (follow-up).** A human comment no longer re-opens a block-loop card to
auto-decomposition: a shipped goal-mode card was re-decomposed into five children
that rebuilt the shipped feature. Now:

- Cards that already ran (`started_at` or any `task_runs` row) or were ever
  escalated by `block_loop_detected` are never decomposition candidates.
  `list_triage_ids` (auto sweep and `decompose --all`) drops them and
  `decompose_task` refuses them unless `force=True`
  (`hermes kanban decompose --force`).
- A worker `needs_input` re-block whose reason changed restarts the recurrence
  count (progress, not a loop). Re-kinded dependency waits still count.
- `unblock_task` / `kanban_unblock` and `promote_task` accept triage → ready
  (parent-gated), so no raw SQL is needed to resume such a card.

## How it works

`awaiting_human_after_block_loop` finds the highest event ID for that card's
`block_loop_detected` events, then checks for a higher-ID event of kind
`commented`, `edited`, or `status`. No escalation means no hold; no qualifying
later event means a hold. `list_triage_ids(skip_awaiting_human=False)` preserves
existing callers by default. The dispatcher alone requests `True` for its sweep.

## Code

- `hermes_cli/kanban_db.py`: `_HUMAN_INPUT_EVENT_KINDS` and
  `awaiting_human_after_block_loop`.
- `hermes_cli/kanban_decompose.py`: optional triage-list filter.
- `gateway/kanban_watchers_dispatcher.py`: `auto_decompose_tick` opts in.
- `tests/hermes_cli/test_kanban_block_kinds.py`: escalation and sweep regressions.
- 0146: `kanban_db.decomposition_refusal`, `_route_block` reason-change reset,
  triage → ready in `unblock_task`/`promote_task`; `kanban_decompose.py` `force`;
  `kanban.py`/`kanban_parser.py` `--force`; `tools/kanban_tools*.py` unblock copy;
  `website/docs/user-guide/features/kanban.md`.
- `tests/gateway/test_kanban_auto_decompose_secret_scope.py`: mock accepts the
  new keyword while retaining launch-profile secret-scope coverage.

## Tests

```sh
scripts/run_tests.sh tests/hermes_cli/test_kanban_block_kinds.py tests/gateway/test_kanban_auto_decompose_secret_scope.py
```

Added cases cover skipping escalations but not ordinary triage, an unchanged
explicit listing, a later comment restoring eligibility, and the dispatcher
requesting the skip flag. Both files passed in the full-series regression run (October 16, 2026 PT).

0146 adds six cases to `test_kanban_block_kinds.py`: a human comment after
escalation does not re-enable auto-decompose, the decomposer refuses a card that
already ran, the CLI threads `--force`, a `needs_input` re-block with a new
reason is not a loop, unblock moves a block-loop triage card to ready, and
promote moves triage to ready with an audit event. The file passed (12 tests) on
fork `origin/main` at `2b64689291` (October 19, 2026 PT).

## Applying

Apply the ordered patch prefix `0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100` from this mirror (or the complete
`0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105` series) to the fork's documented merge-base in a disposable checkout.
This feature is patch `0100`; isolated application is not claimed or tested here.
Run the tests above from the resulting Hermes source root, not this mods repository.

## Gotchas

- “Human input” is an event-order heuristic, not author authentication: the query
  checks event kinds and IDs, not who wrote them. A later automated comment/edit/
  status event also satisfies it. Worker comments normally precede escalation.
- After 0146 a card that ever ran cannot be auto-decomposed again even after
  human input; use `hermes kanban decompose --force <id>` deliberately.
- The helper does not itself verify current status; its caller selects triage
  cards first. This is not a global prohibition on running or moving a card.
- The patch preserves the existing block-loop detector; it changes what the
  automatic decomposer does after an escalation, not the detection threshold.

## Plugin?

No. The guard belongs in the core dispatch sweep and database event query, before
an automatic decomposition is launched.

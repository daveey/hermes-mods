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

Apply the ordered patch prefix `0001–0100` from this mirror (or the complete
`0001–0105` series) to the fork's documented merge-base in a disposable checkout.
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

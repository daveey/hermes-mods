# Preserve Kanban block-loop escalations for human input

Patch: `0100`.

## Behavior

The dispatcher's automatic decomposition sweep leaves a triage card alone after
`block_loop_detected` until a later comment, edit, or status event occurs. This
prevents auto-decomposition from immediately re-promoting the same blocked work.
Ordinary triage cards remain eligible. Explicit specification or decomposition of
one card is unchanged.

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
- `tests/gateway/test_kanban_auto_decompose_secret_scope.py`: mock accepts the
  new keyword while retaining launch-profile secret-scope coverage.

## Tests

```sh
scripts/run_tests.sh tests/hermes_cli/test_kanban_block_kinds.py tests/gateway/test_kanban_auto_decompose_secret_scope.py
```

Added cases cover skipping escalations but not ordinary triage, an unchanged
explicit listing, a later comment restoring eligibility, and the dispatcher
requesting the skip flag. Both files passed in the full-series regression run (October 16, 2026 PT).

## Applying

Apply the ordered patch prefix `0001–0100` from this mirror (or the complete
`0001–0105` series) to the fork's documented merge-base in a disposable checkout.
This feature is patch `0100`; isolated application is not claimed or tested here.
Run the tests above from the resulting Hermes source root, not this mods repository.

## Gotchas

- “Human input” is an event-order heuristic, not author authentication: the query
  checks event kinds and IDs, not who wrote them. A later automated comment/edit/
  status event also satisfies it. Worker comments normally precede escalation.
- The helper does not itself verify current status; its caller selects triage
  cards first. This is not a global prohibition on running or moving a card.
- The patch preserves the existing block-loop detector; it changes what the
  automatic decomposer does after an escalation, not the detection threshold.

## Plugin?

No. The guard belongs in the core dispatch sweep and database event query, before
an automatic decomposition is launched.

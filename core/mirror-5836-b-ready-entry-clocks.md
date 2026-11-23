# Ready-entry age registry and refusal observations

Source patch ordinals: 5547, 5628.

## Behavior

Manual promotion and spend-hold promotion reset a card's current ready-wait age. A preclaim review refusal is an observation, not a fresh ready entry, and must not reset that age.

## How it works

`READY_ENTRY_EVENT_KINDS` centralizes lifecycle entry events. `READY_WAIT_FLOOR_KINDS` adds execution observations (`claimed`, `spawned`) for readers that bound a now-unclaimed interval. Diagnostics, admission score and chat-wait consumers share the registry; the diagnostics facade preserves its compatibility export. Tests inventory status writers and explicitly classify non-entry refusal events.

## Code

- `hermes_cli/kanban_ready_events.py`
- `hermes_cli/kanban_diagnostics.py`
- `hermes_cli/kanban_admission_score.py`
- `hermes_cli/kanban_chat_wait.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/hermes_cli/test_kanban_ready_entry_registry.py` covers manual/spend promotion, exactly-once long-wait notice, registered entry floors, detection of an unregistered writer kind, and `review_admission_refused` preserving ready age.

## Applying

Apply the shared registry and consumer imports together. The later refusal classification belongs with the review-admission series. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

The registry is neither reopen authorization nor block-release policy. `continued` requires an explicit ready status; generic `status` observations do not establish ready entry. An unused grant is not execution.

## plugin?

No. The clock semantics belong to core lifecycle readers.

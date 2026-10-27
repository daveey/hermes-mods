# Durable exact-owner prompt admission

Patches **0441**, **0442**, **0443**, **0444**, **0446** (sources `a0c15d5af9`, `957b76cb38`, `991588aed4`,
`5012b9868d`, `1f220668a6`).

## Behavior

- `prompt.submit` style sends to a named owner session are admitted through a durable receipt: accept, retry and
  readback return the same decision, and a crash after commit never re-executes the prompt.
- A missing target or a bad digest writes nothing; a transcript failure rolls the receipt back; competing handles
  have exactly one winner; cancellation is atomic and replay-proof.
- Reopening an archived owner retires its queue, unarchives it and freezes the reset child; a live session does not
  retire its current queue.
- External transports cannot admit or probe; a refused owner leaves no message.
- The committed decision is projected to the owning Desktop session as an `admitted` user-row event that paints
  before the turn completes, once, keeps distinct rows with identical text, and updates only the bound background
  session. A cancelled dispatch never projects a permission.

## How it works

`hermes_state_prompt_admission.py` holds the receipt table and state machine on top of `hermes_state_sessions.py`;
`tui_gateway/methods_prompt.py` (+ `methods_prompt_admission.py`) exposes it as RPCs declared in
`tui_gateway/contracts/prompt_voice.py` and `events.py`. The design note lives in `tui_gateway/PROMPT_ADMISSION.md`.
The Desktop half is `gateway-event/admitted-message.ts`. 0442 is the follow-up commit with the same subject as 0441 (same
file set); 0444 narrows the event payload to the generated wire type; 0446 fixes the code-health gate without
changing the guards.

## Code

`hermes_state_prompt_admission.py`, `hermes_state_sessions.py`, `tui_gateway/{methods_prompt,methods_prompt_admission}.py`,
`tui_gateway/contracts/{prompt_voice,events}.py`, `apps/shared/src/gateway-contract.*`,
`apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/{admitted-message,index}.ts`.

## Tests

- `tests/hermes_state/test_prompt_admission*.py` (atomic accept/readback, rollback, cancellation, races, legacy receipt migration): state-store path.
- `tests/tui_gateway/test_prompt_admission*.py` and `test_prompt_admission_event.py`: RPC path, external-transport refusal, event ordering and retry without re-emit.
- `use-message-stream/admitted-user-event.test.tsx`: Desktop projection (once, ordering, distinct rows, bound background session).
- Terminal and `execute_code` entry paths are untested here.

## Applying

`git am -3 core/patches/0441-*.patch` through `0446`; then regenerate contracts with `scripts/gen_gateway_contracts.py`.

## Gotchas

- 0441 and 0442 are near-duplicates by subject; keep both, 0442 is the later content.
- Every new RPC needs a contract entry or the contract-coverage test fails.

## Plugin?

No: durable receipts and the transcript projection are core state-store and Desktop event code.

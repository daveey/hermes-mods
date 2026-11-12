# A pinned route's quota wall shows its reset and Choose a model

Patches: **2516**.

## Behavior

A chat pinned to a subscription route that is quota-walled is refused before the turn with `failure_reason: route_refused` and the backend's own sentence (which route, when it resets, `/model`). Desktop used to draw the generic gateway "internal problem ... Send your message again" card, and the transcript kept "Your request was not processed. Send it again", advice that cannot work until the reset. Now the card shows the backend sentence as its body under a dedicated title, its one action is Choose a model (no Retry), and a reopened chat shows the same card. The messaging failed-turn notice for this refusal is just "Your request was not processed." with no resend advice.

## How it works

- `agent/error_surface.py`: for reasons in `_OWN_SENTENCE_REASONS` (`route_refused`), `build_error_surface_from_result` puts the turn's `final_response` in the descriptor's `message`.
- `gateway/run_turn_runner.py` `_resolution_failure_result` builds the failed, never-run result and, for a route refusal, stamps `error_surface` and `refusal_notice` (`ROUTE_REFUSED_TURN_NOTICE` in `agent/turn_failure_copy.py`). `gateway_refusal_boundary` (`session_settlement_recovery.py`) writes `refusal_notice` as the boundary copy and commits the card in display metadata; `run_turn_persistence.py` returns the same notice on the messaging path. See [provider-refusal-turn-boundary.md](provider-refusal-turn-boundary.md) and [kanban-provider-quota-walls.md](kanban-provider-quota-walls.md) for related refusal and quota handling.
- Desktop: `route_refused` is an error code with `choose_model` as its primary action (`lib/error-surface.ts`), with titles and bodies in every shipped locale and `locales/_keys.desktop.json`; `error-surface-copy.ts` treats it as an own-sentence code. `withRefusalSentence` in `message-stream.ts` `turnFailure` covers an older backend's frame without `message`: the turn text minus an appended failed-turn notice becomes the body.

## Code

`agent/error_surface.py`, `agent/turn_failure_copy.py`, `gateway/run_turn_runner.py`, `gateway/run_turn_persistence.py`, `gateway/session_settlement_recovery.py`, `locales/_keys.desktop.json`; Desktop `use-message-stream/gateway-event/message-stream.ts`, `lib/error-surface.ts`, `lib/error-surface-copy.ts`, and the nine locale files under `apps/desktop/src/i18n/`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/gateway/test_route_refused_card.py` (result carries the sentence as card body, settlement commits boundary and card, messaging notice drops resend advice, other refusals unchanged, live completion frame). Desktop: `route-refused-completion.test.tsx` (live frame with and without `message`, generic control), `assistant-message.test.tsx` (rendered card, Choose a model, no Retry), `chat-messages.test.ts` (reopened boundary row). Messaging-platform delivery and non-English rendering are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Older backends send `route_refused` without `message`; Desktop falls back to the turn text.
- Existing transcripts keep their stored boundary copy.

## Plugin

Core change; no plugin required.

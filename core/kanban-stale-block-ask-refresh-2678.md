# Queued Kanban block notice: re-read the card before delivering its ask

Patches: **2666–2675**.

## Behavior

- A Kanban wake is rendered when a card blocks and then waits in the chat's FIFO until the running turn ends, which can be hours. Before the queued row becomes a turn (or a note), the gateway now re-reads the card, read-only. If the card moved past the notice's block, the `Next action for the user:` and `Why only the user:` lines (and the user-terminal hint) are replaced by one line, e.g. `Since then: unblocked 20:08, completed 20:25 PST. No action needed.` The notice's header, `Scope:` line and `Open task:` link stay.
- The replacement line depends on the card's live status:
  - done, archived or otherwise not blocked: `... No action needed.`
  - blocked again with a live human ask that differs from this one: `... This ask was replaced by a newer one; answer that, not this.`
  - blocked or in triage with no live ask (for example unblock then give-up): `... This ask no longer applies; the card is blocked on something else.`
- Times are Pacific, with the zone label printed once, or per time when the window crosses a DST change. A day prefix is added for events on another day than the block.
- Left byte-identical: a card still on the same block (including a re-block with the same ask, e.g. into triage after `block_loop_detected`), a card the gateway cannot read, a remote card (`@task:<board>@<gateway>/...`), an unknown card, and completion results.
- Only producer rows are refreshed (`native_text_v1.automation` / `local_automation_v1`). A notice a person typed or pasted stays as typed.
- Never costs the wake: any error in the refresh is logged and the text is delivered unchanged.
- Fail-closed limits (documented in the code): only quotes inside an owned ask's span are protected, so a stale ask quoted elsewhere (e.g. another notice's reason) is refreshed, which stays truthful; a remote or unreadable link anywhere among the scanned links leaves the whole text unchanged, even an earlier stale ask.

## How it works

- 2666: new `hermes_cli/kanban_stale_ask.py` with `refresh_stale_ask` and `refresh_payload`; `execute_admission` in `gateway/session_ingress.py` calls it on the claimed row before the turn or note, on every gateway route.
- 2667: restricts to producer rows, adds the catch-all in `refresh_payload`, moves the call off the event loop (`asyncio.to_thread`, since it reads sqlite), adds per-time zone labels across DST, and stops a re-block without a human ask from saying "answer that".
- 2668–2672: the early text-delimiting approach (stop at a blank line or actor line) could be fooled by free-text fields, so it was replaced. A notice's ask is now matched to its card: a card owns an ask only if one of its events' `human_request` renders, in `block_notice()`'s exact layout (ask mark through the `Open task:` link), to that text. No field parsing; the scan is `str.find`/`startswith`, linear in the text; the `Open task:` link regex cannot backtrack on long task tokens. The user-terminal hint is removed together with a stale ask and kept with a live one.
- 2673: a quoted ask inside an owned ask's span is never swapped; the first ask no card verifiably owns ends the scan; `block_loop_detected` is treated as a block event (it carries the ask and sets status triage); the ask-mark scan is linear on a long line of marks. An ask equal to the card's live `human_request` is never refreshed.
- 2674: "replaced by a newer one" is said only when the card has a live ask (`tasks.human_request`); otherwise "no longer applies". Fail-closed limits documented.
- 2675: the board is opened through `kanban_lockguard.connect` (read-only URI, 2 s timeout) so the lockguard test passes.
- Bounds: more than 32 asks or 32 `Open task:` links in one text leaves it unchanged; overlapping swap spans leave it unchanged.

## Code

`gateway/session_ingress.py`, `hermes_cli/kanban_stale_ask.py` (new).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/gateway/test_kanban_stale_block_ask.py`. Entry path: a block wake admitted through `gateway.wake.deliver_wake` on a local session, the card changed, then the queued row run through `gateway.session_ingress.execute_admission` (done card drops the ask, still-blocked card keeps it, re-block points at the newer ask); the rest call `refresh_stale_ask` / `refresh_payload` directly (remote and unreadable cards, human-typed rows, stray ask marks, joined notices, quoted scopes and forged heads, long tokens and long lines of marks bounded, terminal hint, same-ask re-block into triage, give-up without a new ask, unexpected refresh errors). Other gateway routes (Slack, Telegram, API) are **untested**, as is a real board with concurrent writers and the `kanban_lockguard` wiring beyond the call itself (2675 has no test of its own).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Time labels are always Pacific, regardless of the viewer's zone.
- A notice whose ask cannot be tied to exactly one readable local card is delivered unchanged, so a stale ask can still reach the chat in those cases.
- The refresh only changes the delivered text; the stored admission row and the card are not modified.
- Wake folding ([kanban-wake-fold-2678.md](kanban-wake-fold-2678.md)) rebuilds batches from stored cards; this refresh runs separately at execution time on the claimed row.

## Plugin

Core change; no plugin required.

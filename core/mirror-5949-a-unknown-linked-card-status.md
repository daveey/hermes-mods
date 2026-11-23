# Unreadable linked cards report an unknown status, not live work

Patch coverage: **5925**.

## Behavior

When a session's canonical outcome lists a linked card whose data is marked stale, partially unknown, or has status `unknown`, the card is recorded as a wait with state `source_unknown` instead of copying its last status. It is never treated as a human ask or as running work. The session verdict becomes `waiting` with reason code `linked_status_unknown`, a neutral (`none`) stripe and `unknown` coverage. A genuinely running card, or the session's own live turn, still wins. An unresolved `source_unknown` verdict (for example after a failed card read) now uses the neutral stripe instead of `attention`.

## How it works

`_read_card_items` checks the stale/partial/unknown markers before human-ask handling. `canonical_status` turns any `source_unknown` wait into a card read error so coverage degrades to unknown. `_verdict` adds `source_unknown` to the open states, ranks it below running and above external waits, and maps an unresolved `source_unknown` reason to the `none` stripe. Recovery needs no manual repair: the next refresh with readable data yields the normal verdict.

## Code

- `hermes_state_outcome.py`: retained-row check in `_read_card_items`.
- `hermes_state_session_status.py`: `source_unknown` open state, waiting verdict, neutral stripe.
- `hermes_state_session_status_snapshot.py`: coverage error when any wait is `source_unknown`.

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live deployment result is claimed.

`tests/hermes_state/test_session_status_refresh.py` covers: unavailable cards for several statuses and both failure markers (not live, not a human ask, then recovery); a real running card winning over an unavailable one; card-read timeouts and link errors giving a neutral unresolved verdict that recovers; and the unreadable-process-inventory case now expecting the neutral stripe. Untested entry path: UI rendering of the new reason code (label mapping is outside these patches).

## Applying

Apply the covered patches only as part of the full ordered core series, following the whole segmented recipe in the [core README Applying section](README.md#applying) (replay bridges and required repair overlays included). The mirror is append-only and these entries depend on earlier ones; do not run a standalone `git am` on them and do not treat them as an independent cherry-pick set. A reimplementation on a newer upstream should keep the invariants above and run the named suites in a disposable source checkout.

## Gotchas

- The marker check must stay before any status or ask interpretation, otherwise a retained "blocked" row can still raise a human ask.
- A stored unknown outcome does not refresh itself when the source heals; see [the links-health recovery doc](mirror-5949-a-links-health-outcome-recovery.md).
- Panel labels by reason are a separate consumer; this change only supplies the typed state.

## Why not a plugin

No: the outcome classifier and verdict reducer are core state modules read by every surface; a plugin would add a second classifier.

# Desktop reload resumes the current streaming segment from a snapshot

Patch: 1333.

## Behavior

- A fresh canonical `session.resume` receives the current assistant segment's text, including text streamed while the viewer was detached, followed by subsequent deltas once. Both included-history and omitted-history backend replies are covered.
- Cold reload and warm activation merge a stamped snapshot with deltas that arrive before the RPC answer, including buffered deltas. The next delta continues the same reply row rather than creating a second bubble.
- Overlapping stamped ranges are counted once by offset, not text similarity. Tool commentary remains intact; an already committed reply is not repeated by an older snapshot.
- A clean `pagehide` flushes a throttled journal tail immediately. A turn that settled before unload is not resurrected.

## How it works

`SessionEvents` tracks current assistant text under its stream lock. Subscription snapshots capture `inflight` alongside `last_sequence`; `AuthorityConnection.resume` returns it. The wire shape marks `segment_only: true` and, for stamped text, includes `segment_stamp` with `turn_id`, `segment`, `offset` and `end` in UTF-16 units.

New/settled turns, interim seals and tool/null-text boundaries clear the tracked text. Overlapping resends append only unseen units; an older segment is ignored. A gap restarts the claimed span, and unstamped text claims no offset identity.

Renderer text parts retain a closing `streamSpan`. Current-segment reconciliation replaces only trailing prose after tool/reasoning boundaries, grafts early runtime deltas onto cold snapshot rows, and merges snapshots under concurrently adopted warm rows. Warm activation targets a newly created pending snapshot row when no live row was cached. The journal overlay carries the projection's span when its raw text permits it.

## Code

- `gateway/{session_events,session_authority,session_contract,session_controls}.py`.
- `tui_gateway/contracts/{sessions,canonical_sessions}.py`; generated shared gateway contracts.
- `apps/desktop/src/app/session/hooks/use-session-actions/{current-segment,resume-reconcile,early-clarify-projection,utils,index}.ts`.
- `apps/desktop/src/app/session/hooks/use-message-stream/index.ts`.
- `apps/desktop/src/lib/chat-messages/{stream-span,parts,types,index}.ts`, `src/lib/inflight-turn-journal.ts`, `src/types/hermes.ts`.

## Tests

These are source-diff coverage references, not executed results from this documentation task. Desktop paths below are relative to `apps/desktop/`.

- `tests/gateway/test_resume_live_text_snapshot.py::test_reattach_gets_head_and_tail_then_future_deltas_once` (both history shapes); `test_settled_or_new_segments_are_never_resurrected`; `test_live_text_is_per_session_stream`.
- Same Python file: `test_resume_snapshot_names_the_utf16_span_of_stamped_deltas`; `test_resent_overlapping_delta_is_not_counted_twice`; `test_a_boundary_restarts_the_span_at_the_next_stamped_delta`; `test_unstamped_deltas_claim_no_identity`; `test_a_stale_segment_or_a_gap_never_widens_the_claimed_span`; `test_stamped_text_after_unstamped_text_claims_no_span`.
- `src/app/session/hooks/reload-resume-early-delta.test.tsx`: `keeps a delta past the snapshot %s, and the next delta extends the same row`; `keeps a delta past the snapshot %s with no journaled row (app restart)`; `keeps only the unseen part of an early delta that overlaps the snapshot`; `adds nothing for an early delta the snapshot already covers`; `drops a replayed copy of the snapshot range that arrives after the answer`. Cases use both stored and new runtime IDs.
- `src/app/session/hooks/warm-activate-early-delta.test.tsx`: `keeps the snapshot and a delta past its cut %s`; `continues the snapshot row with the next delta when the cache held no live row`; `takes the snapshot when no delta beat the answer` (included and omitted history).
- `src/app/session/hooks/use-session-actions/current-segment.test.ts`: `upserts the canonical current segment without repeating narration or tools`; `ignores a snapshot cut before the turn settled when the durable reply already closes with that text`; `puts the snapshot under a run that starts past its cut, keeping the MEDIA source`; `keeps the row text before a snapshot whose span starts past zero`.
- `canonical-segment-resume.test.ts`: `merges durable commentary with the warm live row before upserting the canonical segment`.
- `src/lib/chat-messages/stream-span.test.ts`: `drops a chunk the part already holds and keeps only the unseen tail of an overlap`; `counts UTF-16 units, so an astral character overlaps exactly`.
- `src/lib/inflight-turn-journal.test.ts`: `writes the pending tail when the window unloads inside the throttle`; `does not resurrect a settled turn when the window unloads`.
- `e2e/core/reattach-running-turn.spec.ts`: `re-attaching to a running tool turn renders each segment once` now gates resume so the held tail streams before subscription, then checks the rendered reply exactly once.

## Applying

Apply 1333 in the ordered fork series with its preceding gateway/streaming dependencies. Deploy the matching backend and Desktop renderer through the normal update path. No configuration or hotkey changes.

## Gotchas

This is a current-segment snapshot, not a whole-turn dump or a replacement for durable history. Unstamped legacy projections retain their separate fallback. Crash/forced-kill journal flushing and mixed-version live deployments are untested here; `pagehide` coverage establishes clean unload only.

## Plugin side

None. Snapshot production, wire contracts, stream admission and transcript reconciliation are core responsibilities.

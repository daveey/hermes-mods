# State: missing turn-lease row is reported as its own custody failure, still fail-closed

Mirror entries 5132, 5133, 5134.

## Behavior

Entry 5132 first made `append_message` reclaim a lease row that was missing for the matching holder (the way an expired row already renews in the same transaction). Entry 5133 rejects that: absence of a row cannot tell an unexpected loss from a release, a successor that released, a stale writer or a writer that never acquired, so INSERT-on-append would let a stale writer write. The shipped behavior after 5133:

- Transcript writes stay fail-closed when the lease row is missing or held by a different holder; renewal of an expired row that still matches the writer is kept.
- The refusal reports *missing lease row* versus *holder mismatch* explicitly instead of one generic bucket.
- The user-facing copy no longer asserts "another process took over" from the generic `turn_lease` error bucket.
- 5134 is comment-only: custody comments are shortened to stay inside the per-file line ratchet. No behavior change.

## How it works

- `hermes_state_messages.py`: the lease check in the append path distinguishes the two failure reasons and keeps the matching-row expiry renewal inside the same transaction.
- `hermes_state_user_copy.py`: maps the custody failure to neutral wording rather than a takeover claim.
- Tests encode the intended contract: release, successor-release and never-acquired writers must raise, not reclaim.

## Code

- `hermes_state_messages.py`
- `hermes_state_user_copy.py`

## Tests

Source tests (not executed in this mirror pass):

- `tests/hermes_state/test_session_turn_lease.py`: `test_turn_lease_reclaims_missing_row_for_matching_holder` and `test_turn_lease_revives_expired_row_still_owned_by_writer` (5132, the reclaim contract that 5133 then reverses for the missing-row case); `test_missing_lease_refuses_stale_writer` (5133).
- `tests/hermes_state/test_turn_lease_revocation.py`: `test_late_flush_after_successor_release_on_independent_connection` (an independent connection, event-ordered late flush after a successor released).

5134 has no test (comment-only). A production deployment of this fix is untested here.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Read 5132 and 5133 together: the 5132 reclaim test is replaced by the fail-closed contract; do not infer "missing row is reclaimed".
- The commit text states the root cause of the original failure was undetermined; the diagnostic change is what lets a future occurrence be classified.

## Why core instead of a plugin

The lease check lives in the core session store's transaction and the user-facing error copy is core text. A plugin cannot sit inside that transaction or change the refusal reason without wrapping every append.

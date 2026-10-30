# Frozen decisions stop at a new local conversation

Patch **1239**, source `903f425fa63606c267c2b45646beff2a74348d19` (fork PR296).

## Behavior

A queued LOCAL automation decision with the exact durable producer identity
`["user-steps", "<nonempty operation string>"]` belongs to its conversation.
Compression keeps it valid; resetting to a genuinely new conversation cancels it,
including a reset after compression. Cancellation preserves its original payload,
payload digest, admission ID, request ID and FIFO sequence number.

Generic notices, the near-prefix `user-steps-other`, and three-part identities
continue to retarget after reset. This is not a blanket cancellation of automation.
The descriptor identity must match the row's request ID; model text and request-ID
prefixes do not determine the policy.

## How it works

`_conversation_bound_automation` recognizes the closed producer identity.
`retarget_local_automation` returns `(moved, cancelled)` in FIFO order in one write
transaction. It cancels matching queued rows rather than rewriting their target or
digest. An unknown head still prevents retargeting. Generic rows retain the existing
retarget-and-rekey behavior.

`_reset_and_retarget_local` uses the ordered, shielded `tracked_write` completion
callback to settle cancellations through the authority. That publishes terminal
events and resolves admission waiters; moved rows publish pending state. No-op
resets return empty lists.

## Code

- `hermes_state_runtime.py`: identity policy and atomic cancellation/retargeting.
- `gateway/session_automation.py`: reset and authority settlement callback.
- `tests/gateway/test_frozen_decision_reset.py`: behavioral matrix.

## Tests

`test_queued_frozen_decision_target_fence` is a nine-case matrix: compression,
reset, and compressed-reset crossed with generic, near-prefix and three-part
control identities. It checks frozen cancellation versus compression survival,
immutable payload/digest and identifiers, cancellation event and waiter completion,
generic targeting and which FIFO input can next be claimed.

This is gateway admission coverage, not a live Desktop click or plugin-dispatch
end-to-end test. Caller cancellation injected during the write is **untested**;
the shielded callback mechanism is present, but that specific race is not claimed
as exercised by this matrix. See [mirror-1253-verification.md](mirror-1253-verification.md)
for execution results. Earlier source-owner verification reported 25 focused core
tests, 509 plugin integration tests and 15 lint checks; those broader runs are not
repeated or independently certified by this mirror publication.

## Applying

Apply the complete numeric series from its documented merge-base with
`git am -3 --empty=keep`; patch 1239 depends on earlier LOCAL authority and reset
machinery. When reimplementing on newer upstream, preserve the tuple return contract
and settle through authority callbacks, not only a database status update.

## Gotchas / plugin boundary

A cancelled approval is not authority to carry the answer into the new conversation.
Do not broaden the match to a prefix or alter the frozen payload to make it fit.
The policy lives in core because the authority owns queued admission and reset.
This publication changes only the public patch mirror and docs; it neither deploys
nor verifies behavior in a running gateway.

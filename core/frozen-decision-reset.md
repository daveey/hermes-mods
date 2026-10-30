# Frozen decisions stop at a new local conversation

Patches: 1239 1273 1301

Patch **1239**, source `903f425fa63606c267c2b45646beff2a74348d19` (fork PR296).
Caller-cancellation fix: source `a6ccd8dd8a1d86b582bbd4f563d027d0cb1f80e6`
(fork PR309, reviewed/tested head `be106488fd9c97ecbc2ea35ec60302d2bb73694a`).
The PR309 caller-cancellation fix is exported as patch **1273**:
`patches/1273-test-gateway-cancel-frozen-decision-reset-caller-dur.patch`.
Applying patch 1239 alone does not include the lock-lifetime fix described below.

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
resets return empty lists. In PR309, the LOCAL owner's mutation lock remains held
until the write and settlement task finishes, even when its awaiting reset caller
is cancelled. Previously the caller's `async with` released the lock while the
shielded write continued, allowing a drain claim before cancellation committed.

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

`test_caller_cancelled_during_reset_write_still_settles_frozen_decision` blocks
the underlying ordered DB write with events, cancels the awaiting reset caller,
checks that the owner's mutation lock stays held, then permits completion. It
checks one terminal cancellation event, waiter resolution, immutable frozen
payload/digest/admission/request/sequence, generic retargeting and only the generic
row remaining claimable. This directly tests the lock invariant, not a concurrent
live drain claim. Without the lock fix it fails at `assert live.mutation_lock.locked()`;
removing `tracked_write` shielding separately fails the pending-write assertion.

Executed at the PR309 reviewed head:

```text
scripts/run_tests.sh -j 2 tests/gateway/test_frozen_decision_reset.py
10 passed, 0 failed (nine matrix cases plus caller-cancellation injection)
ruff check gateway/session_automation.py tests/gateway/test_frozen_decision_reset.py
All checks passed
```

An independent neutral reviewer reproduced the lock regression RED and approved
the exact head. This is gateway admission coverage, not a live Desktop click or
plugin-dispatch end-to-end test. Cancellation in the non-LOCAL rehome branch is
not covered by this test; patch 1301 fixes and tests it (below).

### 1301: non-LOCAL (native) rehome keeps the old owner's lock past a cancelled caller

Patch **1301**, source `7fbf27b309d3da51a1c6e54a26c61c5e1aca2a2d` (committed directly on fork main).
`reset_and_rehome_messages`' non-LOCAL branch held `live.mutation_lock` with `async with` around a shielded
`tracked_write`. A cancelled caller left the `async with` and released the lock while the shielded write was
still moving queued messaging rows to the fresh session, so the old owner's drain could claim a row about to
be transferred. It now acquires the lock explicitly and releases it in the write's done callback (the PR309
pattern). The done callback also retrieves the write's exception, since a cancelled caller no longer observes
it. Code: `gateway/session_automation.py`.

Test: `tests/gateway/test_rehome_cancel_lock.py::test_caller_cancelled_during_native_rehome_keeps_old_owner_lock`
admits two native (Discord-adapter) messages, suspends `rehome_queued_inputs` on a thread event, cancels the
caller, and checks that the old owner's lock is still held with both admissions pending on the old session.
After release, both admissions are pending on the fresh session and none on the old. Run by this mirror pass
in a detached worktree at `7fbf27b309`: `bash scripts/run_tests.sh -j 2 tests/gateway/test_rehome_cancel_lock.py
tests/hermes_cli/test_launch_probe.py tests/pm/test_worker.py` gave 49 passed, 0 failed, 1 skipped
(platform-gated) on Linux. No RED run on the parent was made here.
See [mirror-1253-verification.md](mirror-1253-verification.md) for the earlier
nine-case execution. Earlier source-owner verification reported 25 focused core
tests, 509 plugin integration tests and 15 lint checks; those broader runs are not
repeated or independently certified by this documentation update.

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

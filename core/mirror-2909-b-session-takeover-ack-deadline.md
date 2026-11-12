# Take over: ACK deadline pins and desktop reset-save refusal

Patches: 2798, 2841.

- **2798** (`1be6458994`, Take over: pin an EPERM writer to the ACK deadline; a failed reset save keeps the 4090 refusal)
- **2841** (`b9b23d3648`, test: take_over keeps the ACK deadline for a live pid stamped with a foreign pid namespace)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- 2798: a same-namespace pid whose `kill(pid, 0)` answers EPERM is not provably alive, so Take over is refused 4090 SESSION_NOT_OWNED at the ACK deadline, not after the live-writer cap. In the desktop `sendIdentityless`, if saving `legacyAttempted=false` after a definite refusal rejects, it warns `[prepared-submission-reset]` and rethrows the original refusal instead of the storage error.
- 2841: pins `_live_local_writer_pid`'s namespace guard: a live pid stamped with a foreign pid namespace keeps the ACK deadline.

## How it works

- Only 2798 changes product code: `apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts`; the rest are tests.

## Code

- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts`

## Tests

- `tests/tui_gateway/test_session_takeover.py` (`test_take_over_refuses_a_same_namespace_writer_it_may_not_signal_at_the_ack_deadline`, `test_take_over_keeps_the_ack_deadline_for_a_foreign_namespace_pid_that_is_live_here`); `submit-legacy-identity.test.tsx` covers the rethrown refusal.

Test files touched by these patches:

- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit-legacy-identity.test.tsx`
- `tests/tui_gateway/test_session_takeover.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Both gateway tests are mutation-killing coverage of existing behavior; they pass without code changes.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: core gateway and desktop app code.

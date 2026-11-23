# Deterministic contended turn-lease regressions

Source patch ordinals: 5538.

## Behavior

Test-only hardening makes turn-admission regressions demonstrate real SQLite contention rather than relying on a timed lock release. It also specifies safe failure when the admitted worker cannot read the resumed target claim.

## How it works

The `busy_writer` fixture holds `BEGIN IMMEDIATE` until `acquire_session_turn_lease` reports contention through `on_contended`. Each contention-dependent case asserts that report occurred. An injected permission refusal checks that admission releases its lease without changing the agent or caller session context or writing a message.

## Code

- `tests/agent/test_contended_admission_retarget.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/agent/test_contended_admission_retarget.py` covers unchanged lease ownership, compression-child redirection, released and non-owner writes, unreadable claims, and the managed `AIAgent.run_conversation` entry with its guarded flush.

## Applying

Apply with the preceding session-turn lease/admission implementation; this ordinal changes tests only. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

The unreadable-claim case models the refusal on `SessionDB`; it does not exercise `RuntimeSessionStore`. A stubbed conversation loop is not a live model conversation.

## plugin?

No. These are core storage/admission regressions, not plugin behavior.

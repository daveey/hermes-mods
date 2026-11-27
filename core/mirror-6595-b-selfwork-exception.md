# Exact-request single-use self-work admission exception

Patch coverage: 6188, 6189.

## Behavior

A narrowly compiled-in rejected self-work request can be reconsidered once after a verified Desktop answer to a home-filed offer. The original request identity, intent and rejection remain unchanged, and the general guard freeze remains in force.

This is not an automatic admission service or a configurable bypass. The source exposes only a module CLI for its fixed target; ordinary envelopes and tool arguments cannot select another request or manufacture the capability.

## How it works

Offer reads authenticated original request/rejection bytes and checks their bound digests. The home records the exact ask/stamp. A default Unblock verified by the existing card-answer runtime records a grant on its event, but admits nothing in that transaction. Reconsider rereads the original and carries a private in-process capability through the ordinary receiver chain; admission spends the grant in its own transaction and reuses an already-open owner.

The transport exposes raw response bytes for strict parsing while preserving ordinary exact-request refusal handling and envelope-corpus compatibility. Capability data is separate from the intake intent/journal. Changed or ambiguous originals, stale offers and unrelated refusals cannot consume the exception.

## Code

Affected implementation and integration paths in the source series:

- `hermes_cli/kanban_card_answer.py`
- `hermes_cli/kanban_card_answer_transport.py`
- `hermes_cli/kanban_origin_owner.py`
- `hermes_cli/kanban_selfwork_exception.py`
- `hermes_cli/kanban_selfwork_guards.py`
- `hermes_cli/kanban_selfwork_intake.py`
- `hermes_cli/kanban_selfwork_owner.py`
- `hermes_cli/kanban_selfwork_receiver.py`
- `tools/kanban_tools.py`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/hermes_cli/test_kanban_selfwork_exception.py`
- `tests/hermes_cli/test_kanban_origin_envelope_corpus.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas
The public mirror's normalized fixed target is a synthetic fixture, not the original private incident. It must remain exact-target and single-use: do not substitute another request, broaden the target predicate, expose a request selector, or treat the fixture as authority to admit live work. The exception replaces only the freeze refusal for absence of an open guard owner; ordinary admission checks still apply.

The implementation is target-bound; no private target identifiers or digests are published here. It has no registered general tool/subcommand and no automatic caller. The verified-click trust limit remains the source install's recorded action store, not stronger hardware attestation. An unblock alone is not admission or a spend renewal.

## Why core, not a plugin?

The exception must integrate with the existing authenticated card-answer boundary and transactional self-work intake; a plugin argument would weaken the capability boundary.

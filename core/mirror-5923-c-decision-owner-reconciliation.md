# Delayed decision notices and stranded spend ownership

Patches: **5909, 5913**. Incremental changes to existing creator-notice delivery and spend-decision routing.

## Behavior

Decision notices instruct the creator agent to reread current card status and newer owner decisions before commenting. An answered or superseded decision must not be reopened by another waking comment. Ordinary and administrative-only notices preserve safety stops, dependencies and admission limits; a running administrative turn is not acceptance progress.

Spend-held triage cards can regain an administrative owner when a prior supervisor closed, disappeared or was never recorded. Ownership repair does not release execution or change source-card status.

## How it works

`notice_text` appends reconciliation guidance to both notice variants, retains the bounded original question/fallback and qualifies ordinary comment advice: comment only if the decision remains outstanding. Administrative-only guidance still forbids releasing or rerunning the root scope. This is prompt guidance, not a new automatic stale-notice suppression protocol.

`ensure_producer` now validates the prior route. A supervisor route is reusable only while its task exists and is nonterminal. Chat routes and explicit administrative exhaustion (`unavailable`) remain routing boundaries; exhaustion is not permission to recursively create owners. A missing/dead route is resolved through the existing producer mechanism and recorded against the same episode.

The triage sweep repairs routes before idle, caps, containment and release filters, but excludes foreign executor copies. Each safe repair is fault-contained so a failed publication can retry later without starving other stops. New supervisor instructions require applying the source disposition before completing the decision card; unavailable authority/capability should retain a non-executing owner with the precise prerequisite.

## Code

- `hermes_cli/kanban_decision_ask.py`: common reconciliation copy.
- `hermes_cli/kanban_spend_decision.py`: route liveness, triage repair and owner instructions.
- `hermes_cli/kanban_triage_sweep.py`: home-only pre-filter repair integration.

## Tests

**Source regression coverage; not executed in this mirror pass.**

- `tests/hermes_cli/test_kanban_decision_notice_reconcile.py`: both notice variants across delivery surfaces, reconciliation wording, no obsolete parked assertion, bounded long questions and fallback text.
- `tests/hermes_cli/test_kanban_decision_ask.py`: creator wake carries current-status reconciliation guidance.
- `tests/hermes_cli/test_kanban_spend_route_repair.py`: closed/null routes replaced without release; idempotent notices; repair before fresh/capped/contained eligibility filters; foreign-copy exclusion; publication failure isolation; retained-owner instructions; resolved episodes do not reopen producers.

No claim is made that a language-model recipient obeyed this guidance in a live session.

## Applying

Use `core/README.md` complete replay with prior spend, protected-stop and creator-delivery dependencies. Port routing repair independently from admission: repair ownership early, retain all original release gates. Notice-copy changes must reach both administrative and ordinary variants.

## Gotchas

A notice is not ownership proof, and a completed decision card is not a source disposition. Replacing a dead owner must not renew spend or clear safety/scope holds. An explicit unavailable route is intentionally terminal for routing recursion. Comments may wake a card but grant no release authority.

## plugin?

Core owns the notice and triage sweep boundaries. Presentation plugins may expose these decisions but must not treat notices or supervisor completion as authority to resume work.

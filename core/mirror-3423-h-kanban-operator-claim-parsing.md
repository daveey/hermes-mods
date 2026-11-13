# Kanban operator claim parsing: be/being, dashes, adverbs

Patches: **3385, 3408**.

## Behavior
- "Claim" detection in operator delivery text: `be`/`being` govern a verb only when nothing but adverbs sits between them, so "To be safe I posted the summary to the card" and "Being thorough I posted ..." read as claims again, while "Queued to be posted" and "is being quickly sent" stay non-claims.
- A hyphen-joined prefix stays part of the verb ("to be re-posted", "to be cross-posted" are non-claims; "Re-posted ..." alone is a claim).
- A hyphen run touching a space or clause edge is a dash boundary, and a glued run of two or more hyphens is also one ("Not done--posted" is a claim); a single intra-word hyphen is not.
- Common `-ly` non-adverbs (reply, supply, family, early, ...) do not govern, so "Being early posted ..." is a claim; other `-ly` non-adverbs remain a documented fail-open.
- The replica caller check runs once per call.

## How it works
- 3385 changes the `be`/`being` window logic and dash handling in `kanban_operator_delivery.py` and runs the replica caller check once per call in `kanban_operator_call.py`.
- 3408 makes `_WORD` capture a hyphen-glued word, adds the `_NOT_ADVERBS` list, and treats 2+ hyphen runs as dashes.

## Code
- `hermes_cli/kanban_operator_delivery.py`
- `hermes_cli/kanban_operator_call.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_consent_provenance.py`: `test_replica_caller_checks_run_once_per_call`.
- `tests/hermes_cli/test_kanban_operator_delivery.py` was changed by both patches; no new `test_` function names were read, so the claim wording cases are untested by the names listed here.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Other `-ly` non-adverbs remain a documented fail-open.

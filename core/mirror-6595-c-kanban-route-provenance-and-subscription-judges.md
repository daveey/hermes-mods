# Run-route provenance labels and subscription-class judge routing

**Scope:** Route receipts say dynamic vs pin; spend review and hub admission select by subscription class; review route observations retained.

**Source ordinals:** 6392, 6393, 6394, 6478

## Behavior

- The run_route receipt reads the card row for a concrete route: an empty stored selector is labelled dynamic, a stored class selector class, and a stored concrete legacy value keeps pin; a failed or missing row read keeps the old label and the receipt is still written. Route, admission and stored state are unchanged.
- Spend review and hub admission no longer bind concrete provider/model pins; both check subscription billing and explicitly request class:standard rather than inheriting the main pin. Hub admission retains the actual judge model, including on cache hits; spend review's stronger managed-route checks are covered by the spend-review series.
- Dynamic review resolution carries already-read wall/hold/live-wall observations with fixed reasons through to the admission receipt, so later quota snapshots cannot rewrite causal fallback.

## How it works

- `kanban_model_tier.py` computes the source label; `kanban_hub_admission.py`, `kanban_spend_reviewer.py`, `kanban_placement.py`, `kanban_parser.py` change route selection; `kanban_review_model.py` carries the observation.

## Code

Files touched by the series (paths relative to the source tree):

- `hermes_cli/kanban_hub_admission.py`
- `hermes_cli/kanban_model_tier.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_review_model.py`
- `hermes_cli/kanban_spend_reviewer.py`
- `tools/kanban_tools_schemas.py`

## Tests

Source test files changed or added by the series:

- `tests/agent/test_hub_admission_route_provenance.py`
- `tests/hermes_cli/test_kanban_review_model.py`
- `tests/hermes_cli/test_kanban_review_resolution_receipt.py`
- `tests/hermes_cli/test_kanban_run_route_source.py`
- `tests/hermes_cli/test_kanban_spend_reviewer.py`
- `tests/test_kanban_hub_admission.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6392-6478, 4 commits: 6392, 6393, 6394, 6478) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Deadlines, malformed-answer refusal and grant/evidence fences are unchanged.
- Fixed-name judge help text was replaced; consumers of the old schema wording must be updated.
- Receipts establish the admission choice, not worker execution; the source author claims no live causal proof of fallback behaviour.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Routing and receipts are produced inside core dispatch and admission.

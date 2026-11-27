# Refusal boundary for refused providers

**Scope:** Final execution fences.

**Source ordinals:** 6524, 6525, 6526, 6527, 6528

## Behavior

- A provider refused by a card's safety history is rejected at final execution boundaries: after pacing-offer consumption, before the real owner spawn, around worker route construction, and at outbound request validation. Unknown history or lost eligible peers fail closed; the boundary never picks a replacement.
- Reviewer resolution and pacing alternate selection filter refused candidates first, so a qualified unrefused peer is tried instead of burning attempts on a refused offer; exhausted refused candidates never fall back to an unresolved profile route.
- Guarded read-only history access is retained (`mode=ro` through the lock guard); cards with no refused providers keep their existing validators. A stale claim, a class selector or `auto`/`none` provider, a refused provider or an ineligible peer is refused. At outbound request time an already-ended run with no successor claimant may finish its reply on an unrefused provider (only the provider fence applies), while a live claim needs a concrete eligible route; review runs skip the peer-availability check. Provider exclusions are context-scoped and can only be added by nested callers.

## How it works

- `kanban_refusal_boundary.py` holds the checks; `agent/model_routing_exclusions.py`, `model_routing_managed.py`, `model_routing_runtime.py`, `agent/managed_worker.py` propagate additive exclusions into allocation and credential preparation; `kanban_review_model.py`, `kanban_llm_pacing_dispatch.py`, `kanban_paced_reroute.py` filter candidates.

## Code

Files touched by the series (paths relative to the source tree):

- `agent/managed_worker.py`
- `agent/model_routing_exclusions.py`
- `agent/model_routing_managed.py`
- `agent/model_routing_runtime.py`
- `gateway/session_kanban_scope.py`
- `hermes_cli/kanban_llm_pacing_dispatch.py`
- `hermes_cli/kanban_model_tier.py`
- `hermes_cli/kanban_paced_reroute.py`
- `hermes_cli/kanban_refusal_boundary.py`
- `hermes_cli/kanban_review_model.py`

## Tests

Source test files changed or added by the series:

- `tests/agent/test_model_routing_exclusions.py`
- `tests/hermes_cli/test_carbon_dispatch.py`
- `tests/hermes_cli/test_kanban_refusal_boundary.py`
- `tests/hermes_cli/test_kanban_refusal_pacing.py`
- `tests/hermes_cli/test_kanban_refusal_review_boundary.py`
- `tests/hermes_cli/test_kanban_review_resolution_receipt.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6524-6528, 5 commits: 6524, 6525, 6526, 6527, 6528) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Refusal history and holds are unchanged; the new checks are additional fences.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Execution core.

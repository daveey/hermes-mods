# Review routing independent of implementation pins

Patches: **2217–2218**.

## Behavior

A concrete implementation model pin no longer automatically selects that model for review. Review handoff metadata can explicitly choose `review_model` and optional `review_provider`; otherwise selection uses a distinct strong model or eligible deep route. Ready/retry pins remain unchanged.

## How it works

The latest review handoff is consulted before generic pins. Deep routes must satisfy the assignee's worker eligibility and provider policy. Review decisions record `implementer_model` in the current-run `model_tier` receipt. Resolution, route or receipt failures refuse admission before spawning without consuming the implementation retry budget. Review quota handling cannot silently fall back to the author pin.

## Code

`hermes_cli/kanban_review_model.py` implements selection. `kanban_model_tier.py`, `kanban_quota_episode.py` and `kanban_db_dispatch.py` integrate receipts, quota and admission.

## Tests

Source coverage, **not run in this mirror**:

- `tests/hermes_cli/test_kanban_review_model.py`: independent selection, explicit overrides, worker eligibility, receipt enforcement and admission failures.
- `test_kanban_class_routing.py`, `test_kanban_shared_blockers.py`, `test_kanban_strong_tier.py` in the same directory: adjusted routing controls.
- `test_kanban_pin_reroute.py`: author quota walls and closed episodes still select an independent reviewer.

## Applying

Use the [README full replay procedure](README.md#applying), including both patches and their prerequisites.

## Gotchas

Explicit review overrides take precedence even when they name the author model: independence is the default, not an override prohibition. A class pin cannot identify the concrete implementer and requires an explicit review handoff. Legacy unpinned review routing remains available.

## Plugin

Core model routing and dispatch admission; no plugin is required.

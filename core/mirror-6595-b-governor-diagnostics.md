# Recorded governor explanations and lightweight why startup

Patch coverage: 6233, 6234, 6235, 6236, 6237, 6250, 6251, 6292, 6293, 6342, 6343, 6351.

## Behavior

kanban governors reports dispatcher governor evidence; kanban why explains a card's first stop and any separately evaluated card overlaps. Board-wide rules are recorded once per tick. Unreached rules and faults are unknown, never implicit passes.

Fresh saved explanations can be read in text or JSON before heavy CLI/dependency initialization, but after normal safety bootstrap. Unsupported selectors, fences, container routing and missing/stale snapshots retain the canonical fallback path.

## How it works

Dispatch records actual caps, host/board use and first refusal in a versioned sidecar. A bounded fair overlap walk asks later card predicates read-only; null overlaps mean not evaluated, and unknown values remain explicit. Global drains/caps/memory facts are distinct from card overlaps. Live read-only explanations do not refresh usage pools and share publication-owner spend exemptions with actual admission.

The fast path validates snapshot version/shape and checks the card status through an indexed read-only lookup without board initialization. Shared rendering keeps text and JSON semantics aligned. Freshness tolerance derives from bounded recent real tick-completion gaps plus configured cadence, with a hard ceiling; malformed tolerances fail closed. Reads do not append completions or advance a decision.

## Code

Affected implementation and integration paths in the source series:

- `hermes_bootstrap.py`
- `hermes_cli/_startup_fast.py`
- `hermes_cli/kanban.py`
- `hermes_cli/kanban_admission_pools.py`
- `hermes_cli/kanban_board_share.py`
- `hermes_cli/kanban_candidate_receipts.py`
- `hermes_cli/kanban_capacity_evidence.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_dispatch_queries.py`
- `hermes_cli/kanban_failure_accounting.py`
- `hermes_cli/kanban_governors.py`
- `hermes_cli/kanban_governors_cli.py`
- `hermes_cli/kanban_governors_explain.py`
- `hermes_cli/kanban_llm_pacing_dispatch.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_spend_pr_owner.py`
- `hermes_cli/kanban_why_fast.py`
- `hermes_cli/kanban_why_render.py`
- `hermes_cli/main.py`
- `pyproject.toml`

## Tests
All entry paths described here are untested in this review; the files below are source references, not fresh execution evidence.

**Source coverage only — not executed in this mirror run.** The following real source test files were inspected. Their assertions describe intended regression coverage, not a fresh pass result or deployment acceptance:

- `tests/hermes_cli/test_kanban_governors.py`
- `tests/hermes_cli/test_governors_review_probes.py`
- `tests/hermes_cli/test_kanban_why_fast.py`
- `tests/hermes_cli/test_kanban_why_launcher.py`
- `tests/hermes_cli/test_governor_freshness.py`

Entry paths beyond those represented by these suites are untested here. No runtime tests, browser tests, service restarts or external protocol calls were performed for this documentation task.

## Applying
Use README's complete segmented replay and mandatory post-replay escape repair together; neither this feature's ordinals nor the immutable mails alone constitute the installation recipe.

Apply the mirror series as a dependency unit using the inventory, replay boundaries and any required overlays in [README.md](README.md). These ordinals are documentation coverage, not an independently cherry-pickable patch set: shared schemas, bootstrap wiring and later hardening depend on surrounding entries. Follow that README for the matching source/base and application verification; this document does not replace its replay recipe. No application, installation or deployment is claimed by this mirror documentation run.

## Gotchas

A fresh-enough recorded decision is still historical, not live admission clearance. No snapshot is not the same as nothing holds it. Memory pressure or host census that was not observed remains unknown. Fast startup does not bypass attestation, boot pinning or routing restrictions.

## Why core, not a plugin?

The actual dispatcher order, bootstrap safety gates and lightweight CLI entry must be instrumented inside core to avoid invented explanations.

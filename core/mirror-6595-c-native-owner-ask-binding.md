# Inert native-owner ask binding and eligibility producer

**Scope:** Read-only canonical binding modules; inert.

**Source ordinals:** 6583, 6584, 6585, 6594, 6595

> This series is **inert**: the modules are not wired to any installed activation, transport, consumer or decision authority.

## Behavior

- Two new modules form an inert, read-only eligibility producer for a native owner adapter: a read-only builder (schema probe plus SELECTs inside a read transaction) that binds the primary owner board, tenant, declared exact choices and a digest of the sorted portable lifecycle identities; and a private module entrypoint that reads one independently authenticated courier request and reports eligibility only. It never creates or migrates a board (the board database is opened `mode=ro`) and never accepts an envelope.
- Refusals use fixed `bb_binding_*` codes (invalid route/request, schema unavailable, owner unverified, target unverified, ask ineligible, lifecycle unverified). Recursion errors while validating the ask or computing the generation are converted into those refusals. Only version, route, task, tenant, generation and choices are returned; the raw ask contributes to the generation digest but is never transported.
- **Inert and read-only: no registration and no application authority.** No courier action, native consumer or Factory response is registered by this series, and it applies, writes or decides nothing. A snapshot is target eligibility, not decision evidence, enrolled-owner proof or execution permission, and may already be stale. It is not installed activation or decision authority.

## How it works

- `kanban_bb_owner_binding.binding(conn, task, *, owner_gateway, owner_profile, board)` freezes one read snapshot (it opens and rolls back its own read transaction only when the caller has none). A future application caller, not this series, would have to hold its own writer reservation through the recheck. `kanban_bb_binding_runtime.produce(request_id)` / `main` wrap it for the private CLI `python -m hermes_cli.kanban_bb_binding_runtime REQUEST_ID`; exit codes distinguish refusal (2), retryable unavailability (3), internal error (1) and wrong usage (64). Eligibility requires the `babybear` tenant, a task created on the owner gateway, status blocked/triage with block kind needs_input/capability, an approval or reply ask with explicit, unambiguous `{label, value}` choices, and a non-empty set of unique UUID lifecycle event identities; the board must have exactly one primary sync state for the owner gateway.

## Code

Files touched by the series (paths relative to the source tree):

- `hermes_cli/kanban_bb_binding_runtime.py`
- `hermes_cli/kanban_bb_owner_binding.py`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/test_kanban_bb_binding_runtime.py`
- `tests/hermes_cli/test_kanban_bb_owner_binding.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6583-6595, 5 commits: 6583, 6584, 6585, 6594, 6595) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Inert: no transport or consumer is enabled by this series.
- Tests (listed above) were inspected from the diffs only and not executed here; the paths they cover are untested in this mirror.
- The builder deliberately reads only: it must not be used as an authority to apply, enroll or answer an ask.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Core store reads.

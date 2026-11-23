# Unchanged-work fallback after a provider refusal

Patch coverage: **5857**.

## Behavior

For a verified provider safety refusal, local Kanban cards may return to ready and retry unchanged on an unrefused eligible provider of the same class. Ordinary delegated subagents similarly retry unchanged goal/context on other class peers. Exhaustion keeps the safety stop; non-refusal errors are not retried by this mechanism.

## How it works

The refused provider is resolved from the run's worker-result or route receipts, and prior `safety_refused` run metadata/receipts form the refusal set. Class peers must be enabled, serving in the host's live pool and not quota/provider-walled. Dispatch resolves a concrete unrefused route, and pre-claim admission fails closed if peers disappear. Local fallback retains the ended refused run and audit events without injecting a refusal note into the card. Primary replicas report provider/fallback coordinates to canonical release rather than locally closing the running row.

The delegate adapter rebuilds children with the original task, schema and images, stopping at the first non-refusal result. Kanban-origin parents are excluded from delegate-level retry so the card path retains ownership.

## Code

- `hermes_cli/kanban_refusal_fallback.py`: plan, eligible peers, requeue, route and exhaustion admission.
- `hermes_cli/{kanban_refusal_receipt,kanban_model_tier,kanban_db_dispatch_admission,kanban_spend_decision}.py`: receipt, routing, claim and notice integration.
- `tools/{delegate_tool_dispatch,delegate_tool_refusal}.py`: delegated child retry.

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live deployment or performance result is claimed.

- `tests/hermes_cli/test_kanban_refusal_fallback.py`: local requeue/next claim, unchanged title/body/skills and comments, retained run evidence, exhaustion and claim-time fail-closed behavior, concrete pins, unknown provenance, ordinary failures and primary release diagnostics.
- `tests/tools/test_delegate_refusal_fallback.py`: unchanged task retry, one attempt per provider, third-provider success, original failure on exhaustion, non-refusal/success controls and Kanban-parent exclusion.
- Primary coverage checks release diagnostics and ownership, not a separately executed remote service deployment.

## Applying

Apply the covered patches in the full ordered core series using the [core applying recipe](README.md#applying), including its replay bridges and required repair overlay. These entries depend on earlier core changes; they are not advertised as an independent cherry-pick set. Reimplementation should preserve the invariants above and run the named source suites in a disposable source checkout.

## Gotchas

Unknown refusing-provider provenance and independent review runs keep the existing stop behavior. Retry eligibility is not clearance of the work and does not reword it. A child with no unambiguous class has no proven peers. Canonical service compatibility remains a prerequisite for synced lifecycle closure.

## plugin?

No: route selection, admission, refusal custody and child construction must share core state.

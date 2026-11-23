# Kanban capsule contract v2 and bounded identify (mirror 5491–5493)

## Behavior
- Legacy same-revision capsule submission is kept; across revisions an explicit v2 contract is required.
- Capsule availability is withdrawn on missing or dirty boot provenance, code skew, unsupported routes and unfiltered ledger unknowns.
- Capsule children are bootstrapped from the owner core, and its private expectation is enforced before execution and at every late startup boundary. Single-use authenticated rendezvous is preserved.
- Advisory identify waits at most 0.5 s for one context-bound verifier and never caches a late positive; command preflight runs off the event loop. Strict owner and child permissions stay uncached. Same-revision legacy handling applies to submitter admission only.
- The new Git status subprocess in `git_fingerprint` (output read capped at one byte, 2 s kill timer, 2 s wait) is added to the worker-drain spawn inventory as `status: pending` (direct 1, routed 0), without claiming drain fencing.

## How it works
`gateway/capsule_contract.py` implements the contract; `hermes_cli/git_fingerprint.py` and `code_attestation.py` supply provenance; hooks in `agent/managed_worker.py`, `gateway/run_bootstrap.py`, `run_runtime.py`, `session_kanban_*.py`, `session_managed_worker.py`, `hermes_bootstrap.py`, `kanban_capsule_owner.py`. Design: `docs/designs/kanban-capsule-contract-v2.md`.

## Code
`gateway/capsule_contract.py`, `hermes_cli/git_fingerprint.py`, `hermes_cli/code_attestation.py`, `hermes_cli/worker_drain_spawn_inventory.json` and the hooks listed above.

## Tests
By test names: `tests/agent/test_capsule_worker_identity.py` (child refuses revision change, wrong/unknown owner identity or a lost expectation; ordinary child needs no capsule identity); `tests/gateway/test_capsule_identify_budget.py` (a slow check withdraws only v2 and never reuses a late positive; context preserved and recovery from an exception; thread-start failure withdraws and releases the slot); `tests/gateway/test_capsule_worker_launch.py` (launch shape parametrized by `required`, and a `preflight` helper added by 5493; the diff shows no test whose name claims an off-loop or real-socket assertion, so those aspects are not verified here); plus availability, integrity-gate, code-identity, attestation, ABI golden, composed cross-revision submitter, native lifecycle, native refusal, owner-contract and late-boundary suites. 5492 adds an inventory entry for `git_fingerprint.py`; no test for it is shown in the diff. Not executed in this mirror pass.

## Applying
Apply all three: 5492 is required for the source-wide spawn-inventory check to pass.

## Gotchas
Fail-closed gates can make capsule availability disappear after a dirty checkout.

## plugin?
No.

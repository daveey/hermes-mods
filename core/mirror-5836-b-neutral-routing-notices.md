# Model-neutral refusal and recovery guidance

Source patch ordinals: 5595, 5602, 5607.

## Behavior

Host refusal, quota-wall and recovery notices describe selected routes and eligible classes instead of prescribing concrete repins. Legacy selector history may still dispatch through a neutral projection without rewriting the stored evidence.

## How it works

Host-refusal wording reports the selected subscription route. Class wall notices use eligible-route language and remove `set-model` repin instructions. Goal-recovery guidance preserves class/tier, capabilities, audit provenance, billing and safety rather than old concrete restrictions. Recovery children inherit the neutral next-admission view.

## Code

- `hermes_cli/kanban_host_refusal.py`
- `hermes_cli/kanban_resume_requester.py`
- `hermes_cli/kanban_dispatch_skip.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/hermes_cli/test_kanban_host_refusal.py` checks refusal text. `test_kanban_goal_recovery.py` covers a neutral child and repeated resume cycles; `test_kanban_refused_pin_clear.py` covers read-only dry-run projection and legacy dispatch without history rewrite. `tests/tools/test_kanban_create_overlap_roles.py` checks rejected pin ingress without local/replica mutations. `test_kanban_dispatch_skip.py` and `test_kanban_pickup_pinned_first.py` cover class walls and pickup refusal.

## Applying

Apply with model-neutral selectors and quota holds; later recovery-evidence serialization and separate-execution review guidance complete the final policy. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

Priority changes do not clear quota walls. Do not perform held work outside the dispatcher or replace a running client to implement a stored routing edit. A historical concrete route in a notice is evidence, not a new routing requirement.

## plugin?

No. These are shared core diagnostic/recovery producers.

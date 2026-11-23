# Lossless historical evidence in recovery and forwarding

Source patch ordinals: 5615–5616, 5618–5620.

## Behavior

Recovery, forwarding and escalation cards can preserve old model-specific instructions as labeled inert evidence without turning them into fresh routing obligations. Ordinary diagnostic text remains valid. Model-specific quota observations do not disqualify a healthy sibling route.

## How it works

`forwarded_text` serializes a rejected historical line as a JSON string after `Historical run instruction (superseded and not active): `. `_active_text` excludes only that string and still checks unquoted trailing text. `forwarded_title` substitutes a neutral title while preserving the original in body evidence. Producers cover continuation/goal recovery, no-progress, assignment repair, capability owners, missing parents, duplicate alarms, pickup stalls, spend decisions, systemic crashes and triage escalation. Forwarding also handles parent handoffs. Class wall probing distinguishes provider-wide walls from individual model deadlines; resolved classes are checked regardless of diagnostic tier reason.

## Code

- `hermes_cli/kanban_model_neutral.py`
- `hermes_cli/kanban_resume_requester.py`
- `hermes_cli/kanban_no_progress.py`
- `hermes_cli/kanban_placement_forward.py`
- `hermes_cli/kanban_assignment_routing.py`
- `hermes_cli/kanban_capability_routing.py`
- `hermes_cli/kanban_pickup_stall.py`
- `hermes_cli/kanban_spend_decision.py`
- `hermes_cli/kanban_triage_sweep.py`
- `hermes_cli/kanban_class_walls.py`
- `hermes_cli/kanban_dispatch_skip.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/hermes_cli/test_kanban_review_edge_probe.py` covers sibling quota scope, no-progress legacy evidence and trailing active mandates. `test_review_neutral_boundary_probe.py` covers both recovery producers and lossless title evidence. `test_review_forwarded_history.py` covers title/body/parent forwarding with table-driven fixtures; `test_review_wall_reason_family.py` covers resolved tier reasons. Alarm/history/kernel/contained-spend suites exercise common create boundaries for escalation producers and unchanged ordinary diagnostics.

## Applying

Apply with ingress validation and all affected card producers; incomplete adoption can leave a producer rejecting a historical title as if it were a new mandate. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

Only genuinely superseded evidence belongs inside the labeled JSON string. Do not quote an active instruction to evade validation. A familiar prefix without valid JSON is not an inert-evidence exemption, and text after the string is still operative.

## plugin?

Core helpers/producers. The evidence wrapper is a representation boundary, not a plugin exemption or execution authorization.

# Validate worker assignments and route bounded repairs

Patches: **0300 0336**; human-block integration follow-up **0301** is documented in [agent recovery](kanban-agent-recovery-routing.md).

## Behavior

Creation and assignment validate a worker profile's configured capabilities against explicit `requires` plus workspace-derived requirements. Directory work needs terminal, file and host-repository capabilities; worktrees also require git. An empty requirements list cannot disable workspace inference. CLI, registered-tool and dashboard REST creation share validation rather than allowing one surface to bypass it.

A proven worker-local mismatch can create one durable supervisor repair card and a dependency instead of asking a human to repair the worker's tools. Authentication, approval, billing and policy decisions remain human requests. Incompatible known model/provider pins are rejected before create, or routed to repair when found in legacy dispatch; explicit pins are not silently changed.

## How it works

0300 reads the target profile's effective CLI tool policy with profile-specific home, secret and terminal scopes, restoring them afterwards. Missing/malformed policy fails closed for constrained assignments. The known capabilities are `terminal`, `file`, `git`, `host_repo` and `kanban`. Unconstrained scratch tasks retain legacy behavior; unassigned drafts defer validation until assignment. Requirements are also retained through decomposition, review and transfer paths.

Assignment repair runs in the block transaction after the run-ownership CAS. A narrow worker-mismatch reason plus independent policy evidence selects repair; unreadable policy alone is not evidence of missing tools. The configured `kanban.orchestrator_profile` or capable creator must be a real, different profile with the original requirements plus Kanban tools. The original waits on an idempotent `assignment-repair:` card. Completion revalidates the repaired assignment. Failed routing rolls back instead of leaving an invisible hold; repeat/self-supervisor chains are refused.

0336 adds model-assignment coherence checks and dispatch-time repair for existing invalid pins. Dry-run diagnostics do not mutate the board, and an unavailable supervisor must not abort unrelated dispatch.

## Code

- `hermes_cli/kanban_capabilities.py`: conservative configured-policy preflight.
- `hermes_cli/kanban_assignment_routing.py`, `kanban_model_assignment.py`, `kanban_block_routing.py`: narrow classification and durable repair.
- `hermes_cli/kanban_db{,_connect,_graph,_dispatch}.py`, `kanban_decompose.py`, `kanban.py`, `kanban_parser.py`: shared lifecycle entry points.
- `tools/kanban_tools{,_schemas}.py`, `plugins/kanban/dashboard/plugin_api.py`: tool and REST requirements.
- `website/docs/user-guide/features/kanban-worker-lanes.md`: source operator guidance.

## Tests

Source regression coverage, **not run by this documentation pass**:

- `tests/hermes_cli/test_kanban_capabilities.py`: effective tool policy, remote backend refusal for host-repository access, explicit empty policy, missing/malformed config, scope restoration, no guessed skill requirements.
- `test_kanban_capability_create.py`, `test_kanban_create_entry_parity.py` in that directory: shared create, registered surfaces, CLI parity, REST `requires`, atomic decomposition and promotion.
- `test_kanban_assignment_routing.py`: durable supervisor, human-access controls, stale-run refusal, rollback, entry-point routing and typed tool failures, policy drift and bounded repair.
- `test_kanban_review_capabilities.py`, `test_kanban_transfer.py`, `test_kanban_worktree_create_validation.py`, `test_kanban_worktree_isolation.py`: review/transfer/workspace integration.
- `test_kanban_model_assignment.py`: create rejection, preserved compatible/open routes, typed child errors, legacy dispatch repair, no-supervisor isolation and read-only dry run.
- `tests/tools/test_kanban_tools.py`: tool regressions.

Run with `bash scripts/run_tests.sh <files>` in an isolated applied checkout. Full-series apply/test results are pending the mirror owner. Actual installed binaries, repository access and live model-provider calls are not verified by these policy tests.

## Applying

Use the complete ordered series: creation, decomposition, block routing and dispatch share helpers with surrounding patches. Apply 0301's human-block/helper fixes with 0300, then 0336's model checks.

## Gotchas

`git` means terminal capability, not a probe that the binary is installed. `host_repo` is established conservatively by local backend policy, not by checking a path on the dispatch host. Skill names/prose do not infer capabilities. A repair card is a dependency and verification task, not permission to execute or duplicate the original work.

## Plugin relationship

The bundled Kanban dashboard plugin exposes shared validation, but the authority lives in core creation/assignment transactions and dispatch. A UI-only plugin cannot enforce entry parity or stale-run fencing.

## Follow-up patch (1498): editing another card's model pin is an authority gap, not a missing supervisor

**1498** (`d6eb66511e`, `fix(kanban): classify model-edit authority before supervisor routing`).

- **Behavior.** A `kanban_block(kind='assignment')` reason asking to change, set, clear, edit, replace or remove ANOTHER card's `model`/`provider`/pin/`model_override`/`provider_override` (for example "Change t_X model pin to an available subscription route", "Use kanban set-model t_X", "t_X needs a model change") is now an execution-authority need, classified before supervisor candidate selection. The block is refused with `ExecutionAuthorityMismatch` (`code: execution_authority_mismatch` via the tool). The message explains that every worker profile has the same mutation fence, that assignment repair cannot grant non-worker authority, and that no supervisor model-edit operation exists. It says to keep mandatory model pins and subscription billing policy and recover through the existing owner or an authorized non-worker operator, and that a legacy repair must be archived rather than completed on profile existence. It does not suggest a `resume` supervisor request. The card's status, assignee and model/provider overrides are unchanged, and no repair card or link is created, whether the configured supervisor is eligible, absent or ineligible.
- **Not an authority need.** The same verbs in history or inspection wording ("The model on t_X was changed yesterday", "Changed model pin on t_X", "Inspect t_X model and provider without changing them", "Need the model output that t_X produced"), and an edit of the blocking card's own pin.
- **How it works.** `_OTHER_CARD_MODEL_EDIT` in `hermes_cli/kanban_assignment_routing.py` matches an imperative verb (whole word, so "changed" does not count) and a task id within one clause (at most 100 characters, no `.`/`;`/newline) in either order, or `<id>('s) needs (a) <field> change|edit|replacement`. `execution_authority_need` merges those ids with the lifecycle-transition ids. `_authority_error(task_id, needs, reason)` picks the model-edit wording when the reason matches. `route()` and `validate_repair_completion()` both pass the reason, so a legacy `assignment-repair:` card created before this patch for a model-edit reason cannot complete and release the original.
- **Code.** `hermes_cli/kanban_assignment_routing.py`.
- **Tests** (new file `tests/hermes_cli/test_kanban_assignment_model_authority.py`; not run for this mirror): `test_model_edit_is_authority_need` (7 phrasings), `test_model_history_is_not_authority_need` (4), `test_own_model_edit_not_other_card`, `test_model_authority_refused_before_candidate_selection` (eligible / absent / ineligible supervisor; DB `kb.block_task` path), `test_legacy_model_edit_repair_cannot_release_original` (`kb.complete_task` on the repair), `test_tool_model_edit_refusal_is_typed` (registered `kanban_block` tool). The CLI `hermes kanban block` path has no test of its own for this.
- **Gotchas.** The classifier is English-verb based and limited to one clause. A request phrased without those verbs (for example "switch t_X to Sonnet") is not caught and still follows the earlier assignment-repair rules.

## Follow-up patches (1632, 1633): offer `kind='assignment'` only where a different profile can take the card

**1632** (`f50d06c612`, `fix(kanban): offer kind='assignment' only where a different profile can take the card`) and
**1633** (`6ea8987d2a`, narrows the guidance probe's `except`).

- **Behavior.** On a single-profile host (no `kanban.orchestrator_profile`, creator == assignee),
  `kanban_block(kind='assignment')` always fails with `no_capable_supervisor`, yet the `agent_owned_block` refusal, the
  `kanban_block` schema, the worker prompt and the awaiting remedy all prescribed it, so workers hit that wall and then
  improvised (asks to a human for agent work, self-parks with no producer). Now:
  - The `agent_owned_block` refusal (tool and CLI) offers assignment only when a different, existing, capable profile is
    configured for that card. Otherwise it lists the routes that work on one profile: (a) `kanban_create` an owner card
    on the gateway that holds the access and park on it with `awaiting={kind:'card', ...}`; (b) `kanban_block(needs=[...])`;
    (c) `kanban_supervisor_request` for archive / unlink / resume / retry_limit on a card in the creator tree. The
    `needs_input` refusal gains route (4), the supervisor request.
  - `route()`'s `no_capable_supervisor` error names those same routes instead of telling the agent to configure a profile.
  - The `kanban_block` schema, `KANBAN_GUIDANCE` and the `agent_action_wait` remedy qualify assignment as "only when a
    different profile is configured".
  - Routing behavior and config defaults are unchanged.
- **How it works.** `kanban_assignment_routing.supervisor_available(task)` is `_supervisor(task)[0] is not None` (the
  same check `route()` uses, no new IO). `NO_SECOND_PROFILE_ROUTES` holds the shared route text.
  `kanban_block_policy.block_policy_error(..., task=(conn, task_id))` calls `_assignment_routes(task)`; with no task or
  on a read error it never offers assignment. The CLI `_cmd_block` now evaluates the policy per card in a bulk block
  (the first refusal stops the whole block, as before) and passes `task=`; the tool does the same for its card. 1633
  narrows the probe's `except Exception` to `(sqlite3.Error, ValueError, OSError)`.
- **Code.** `hermes_cli/kanban_assignment_routing.py`, `hermes_cli/kanban_block_policy.py`, `hermes_cli/kanban.py`,
  `hermes_cli/kanban_awaiting.py`, `agent/prompt_builder.py`, `tools/kanban_tools_schemas.py`.
- **Tests** (new `tests/tools/test_kanban_block_assignment_guidance.py`; not rerun by this mirror pass):
  `test_tool_refusal_on_single_profile_host_omits_assignment`, `test_cli_refusal_on_single_profile_host_omits_assignment`,
  `test_tool_refusal_offers_assignment_when_a_different_profile_is_configured`,
  `test_cli_refusal_offers_assignment_when_a_different_profile_is_configured`,
  `test_refusal_without_a_task_never_offers_assignment`, `test_supervisor_available_matches_route_eligibility`,
  `test_no_capable_supervisor_refusal_names_the_working_routes` (DB `kb.block_task` path),
  `test_block_schema_no_longer_prescribes_assignment_for_agent_work`,
  `test_worker_prompt_and_awaiting_remedy_qualify_assignment`. `tests/tools/test_kanban_agent_owned_block_guidance.py`
  now asserts the single-profile routes. 1633 has no test of its own (an exception other than the three listed would
  now propagate; untested).
- **Gotchas.** Guidance only: assignment routing itself is unchanged, so a card on a single-profile host still cannot
  use it. A bulk CLI block with mixed cards is refused at the first card whose policy refuses.

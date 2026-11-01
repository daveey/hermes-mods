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

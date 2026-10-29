# fix(kanban): agent-created cards must declare requires; skill metadata feeds the preflight [daveey] (#166)

Patch 0809, source `3234f983760f4408b8387ec53b869fcc57750d36`.

## Behavior

fix(kanban): agent-created cards must declare requires; skill metadata feeds the preflight [daveey] (#166). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _cmd_create(args: argparse.Namespace) -> int:`
- `def classify(task, kind, reason, *, human_request=None):`
- `def _supervisor(task):`
- `def validate_repair_completion(conn, task_id):`
- `class CapabilityMismatchError(ValueError):`
- `def _profile_capabilities(assignee: str) -> set[str]:`
- `def validate_assignment(`
- `def create_task(`
- `def assign_task(conn: sqlite3.Connection, task_id: str, profile: Optional[str])`
- `def request_review(`
- `def request_changes(`
- `def reopen_review_task(conn: sqlite3.Connection, task_id: str) -> bool:`
- `def specify_triage_task(`
- `def _validate_children_graph(children: list) -> None:`
- `def decompose_triage_task(`
- `def _insert_decomposed_child(`
- `def _apply_fanout(task_id: str, parsed: dict, routing: _Routing, author: str) ->`
- `def decompose_task(`
- `def create_with_origin(conn, intent: dict, options: dict) -> str:`
- `def _arg(*flags: str, **kw):`
- `_SPECS = [`
- `+"""Agent-created cards cannot omit requires past the assignment preflight. [daveey]`
- `from agent.redact import redact_sensitive_text`
- `def _kanban_handler(tool_name: str, *, classify_rejections: bool = False) -> Cal`
- `def _persisted_session_id(session_id: Optional[str]) -> Optional[str]:`
- `def _handle_create(args: dict, **kw) -> str:`
- `+"""Placement refusals for kanban_create (split out of :mod:tools.kanban_tools). [daveey]"""`
- `KANBAN_CREATE_SCHEMA = _schema(`
- `implies git. Explicit requirements augment these checks, never disable them.`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> * fix(kanban): agent-created cards must declare requires; skill metadata feeds the preflight [daveey]
>
> The assignment preflight (validate_assignment) only checked a profile when
> something named a capability. A scratch card whose agent creator omitted
> requires named nothing, so it went to any profile, including a sandboxed
> lane without the repo or credentials it needed. Omitted and [] were stored
> identically, and pinned skills' structured prerequisites were ignored.
>
> - kanban.require_capability_declaration (default false = upstream): the
>   kanban_create tool (chat or worker), an agent/worker hermes kanban create,
>   and decomposer children refuse an omitted requires that no dir/worktree/
>   project workspace or capability-bearing pinned skill backs. Typed
>   requires_declaration_missing (tool: rejection invalid_args, created false;
>   CLI exit 2). Nothing is written, and the decomposer fan-out stays atomic and
>   is re-asked once with the error. Explicit [] (CLI --no-requires) still
>   creates a capability-free card. Humans (interactive CLI, dashboard), courier
>   envelopes and kernel-made cards keep legacy behaviour.
> - A pinned skill's metadata.hermes.requires_toolsets/requires_tools (the
>   existing activation contract) maps to terminal/file/kanban and is merged
>   into the requirements on create, assign, reassign and review, so [] cannot
>   bypass it. Names and prose are never parsed.
> - Order: declaration, then capability, then model/provider checks.
> - Legacy stored cards are not migrated or redispatched.
>
> Tests: tests/hermes_cli/test_kanban_requires_declaration.py (shared boundary,
> tool chat and worker, agent and human CLI, decomposer plus re-ask, and a real
> CLI subprocess on a disposable HERMES_HOME/DB).
>
> * fix(kanban): gate omitted requires before cross-gateway placement; review follow-ups [daveey]
>
> - kanban_create runs the declaration check before _place_remote: an omitted
>   requires is what routes a card to another gateway, where it lands as an
>   exempt courier create.
> - The courier envelope never stores require_declaration, so an intent recorded
>   before this change still matches on retry.
> - The assignment-routing supervisor/repair checks pass skills too.
> - Tests: routed placement, courier envelope, worker-shell CLI, a legacy row on
>   reassignment, a real declaration > capability > model ordering, and the
>   decomposer giving up after one re-ask / not re-asking other failures.
>
> * fix(kanban): require_capability_declaration is a board policy [daveey]
>
> Shared config writes only the default profile's config.yaml, so a worker of
> another profile (its own HERMES_HOME, no key) creating children went ungated.
> declaration_required() now also reads the board home's config.yaml
> (kanban_home()), through utils.load_yaml_file_readonly.
>
> * fix(kanban): agent-created cards must declare requires; skill metadata feeds the preflight [daveey]
>
> The assignment preflight (validate_assignment) only checked a profile when
> something named a capability. A scratch card whose agent creator omitted
> requires named nothing, so it went to any profile, including a sandboxed
> lane without the repo or credentials it needed. Omitted and [] were stored
> identically, and pinned skills' structured prerequisites were ignored.
>
> - kanban.require_capability_declaration (default false = upstream): the
>   kanban_create tool (chat or worker), an agent/worker hermes kanban create,
>   and decomposer children refuse an omitted requires that no dir/worktree/
>   project workspace or capability-bearing pinned skill backs. Typed
>   requires_declaration_missing (tool: rejection invalid_args, created false;
>   CLI exit 2). Nothing is written, and the decomposer fan-out stays atomic and
>   is re-asked once with the error. Explicit [] (CLI --no-requires) still
>   creates a capability-free card. Humans (interactive CLI, dashboard), courier
>   envelopes and kernel-made cards keep legacy behaviour.
> - A pinned skill's metadata.hermes.requires_toolsets/requires_tools (the
>   existing activation contract) maps to terminal/file/kanban and is merged
>   into the requirements on create, assign, reassign and review, so [] cannot
>   bypass it. Names and prose are never parsed.
> - Order: declaration, then capability, then model/provider checks.
> - Legacy stored cards are not migrated or redispatched.
>
> Tests: tests/hermes_cli/test_kanban_requires_declaration.py (shared boundary,
> tool chat and worker, agent and human CLI, decomposer plus re-ask, and a real
> CLI subprocess on a disposable HERMES_HOME/DB).
>
> * fix(kanban): gate omitted requires before cross-gateway placement; review follow-ups [daveey]
>
> - kanban_create runs the declaration check before _place_remote: an omitted
>   requires is what routes a card to another gateway, where it lands as an
>   exempt courier create.
> - The courier envelope never stores require_declaration, so an intent recorded
>   before this change still matches on retry.
> - The assignment-routing supervisor/repair checks pass skills too.
> - Tests: routed placement, courier envelope, worker-shell CLI, a legacy row on
>   reassignment, a real declaration > capability > model ordering, and the
>   decomposer giving up after one re-ask / not re-asking other failures.
>
> * fix(kanban): require_capability_declaration is a board policy [daveey]
>
> Shared config writes only the default profile's config.yaml, so a worker of
> another profile (its own HERMES_HOME, no key) creating children went ungated.
> declaration_required() now also reads the board home's config.yaml
> (kanban_home()), through utils.load_yaml_file_readonly.
>
> * refactor(kanban): keep the declaration gate inside the code-health ratchet [daveey]
>
> - validate_assignment(require_declaration=) runs the check, so create_task
>   gains no branch; kanban_create calls check_tool_create() before placement
>   and the handler maps any exception carrying tool_error_fields.
> - The courier envelope strips require_declaration in create_with_origin.
> - --requires/--no-requires share one argparse action: omitted stays None,
>   both is a usage error, and _cmd_create gains no branch.
> - _placement_refused moves to tools/kanban_tools_placement.py to offset
>   kanban_tools.py growth.
> - kanban.require_capability_declaration is read with .get(); it is not added
>   to DEFAULT_CONFIG (unset = off = upstream).

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_assignment_routing.py`
- `hermes_cli/kanban_capabilities.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_graph.py`
- `hermes_cli/kanban_decompose.py`
- `hermes_cli/kanban_origin_creation.py`
- `hermes_cli/kanban_parser.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_placement.py`
- `tools/kanban_tools_schemas.py`
- `website/docs/user-guide/features/kanban-worker-lanes.md`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_requires_declaration.py`

New or changed test definitions present in the source diff:

- `test_db_refuses_omitted_requires_on_unbacked_scratch_card`
- `test_structured_worktree_job_needs_no_declaration_and_is_enforced`
- `test_explicit_empty_creates_capability_free_card`
- `test_explicit_empty_cannot_bypass_skill_metadata`
- `test_explicit_empty_cannot_bypass_repo_workspace`
- `test_skill_metadata_backs_an_omitted_declaration_and_is_stored`
- `test_skill_requirements_read_structured_fields_only`
- `test_reassignment_rechecks_skill_needs`
- `test_reassigning_a_legacy_card_rechecks_its_pinned_skills`
- `test_check_order_declaration_then_capability_then_model`
- `test_gate_off_keeps_legacy_behaviour`
- `test_board_policy_reaches_other_profiles_workers`
- `test_tool_chat_create_omitted_requires_is_typed_and_writes_nothing`
- `test_tool_explicit_empty_and_gate_off`
- `test_worker_created_child_is_gated`
- `test_tool_gate_runs_before_cross_gateway_placement`
- `test_courier_envelope_does_not_carry_the_gate`
- `test_cli_agent_create_parity`
- `test_cli_interactive_human_keeps_legacy`
- `test_cli_from_a_worker_shell_is_gated`
- `test_decomposer_children_must_declare`
- `test_decompose_task_reasks_once_with_the_error`
- `test_decompose_task_gives_up_after_one_reask`
- `test_decompose_task_does_not_reask_other_failures`
- `test_real_cli_process_uses_disposable_db_and_refuses`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

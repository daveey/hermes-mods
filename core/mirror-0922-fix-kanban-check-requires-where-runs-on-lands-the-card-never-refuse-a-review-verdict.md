# fix(kanban): check requires where runs_on lands the card; never refuse a review verdict [daveey]

Patch 0922, source `f46e5f476c1bb56b4f9a69031327bb6d8bc96704`.

## Behavior

fix(kanban): check requires where runs_on lands the card; never refuse a review verdict [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _profile_capabilities(assignee: str) -> set[str]:`
- `def create_task(`
- `def request_changes(`
- `Guards:`
- `def check(conn: sqlite3.Connection, task_id: str, runs_on: Any, *, force: bool =`
- `+"""requires checked against the machines a card's runs_on can land on. [daveey]`
- `def pickup(conn: sqlite3.Connection, *, budget: Optional[int],`
- `def test_explicit_human_evidence_preserves_access_boundary(board, blocker_id, pa`
- `+"""requires is checked where a card runs, not only where it was created. [daveey]`
- `def _handoff(conn, tid, operation):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> t_01a184ac93d31979 (requires [terminal,file,git,kanban], runs_on machine:worker-1) passed create on the
> MacBook's profile, ran twice on worker-1 unchecked, then the reviewer's kanban_request_changes failed
> assignee_capability_mismatch against worker-1's `default` and became a human ask.
>
> - kanban_runs_on_capabilities: create and `kanban move` refuse when every machine runs_on can land on is
>   KNOWN (local policy or published profile-tools) to lack a needed tool, naming machine and tool; unknown
>   machines never refuse.
> - worker-node pickup: a machine never claims a card its own profile cannot satisfy (left on the Board).
> - request_changes records the verdict for the implementer that ran the card; a mismatch is a warning in
>   the changes_requested event.
> - _profile_capabilities: `kanban` is held unless agent.disabled_toolsets removes it, because every
>   dispatched worker gets the kanban toolset (model_tools) whatever platform_toolsets.cli says.

## Code

- `hermes_cli/kanban_capabilities.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_move.py`
- `hermes_cli/kanban_runs_on_capabilities.py`
- `hermes_cli/kanban_worker_node.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_assignment_routing.py`
- `tests/hermes_cli/test_kanban_requires_placement.py`
- `tests/hermes_cli/test_kanban_review_capabilities.py`

New or changed test definitions present in the source diff:

- `test_create_for_a_machine_known_to_lack_a_tool_is_refused_naming_it`
- `test_create_passes_when_one_matching_machine_is_capable_or_unknown`
- `test_tool_create_refusal_names_the_machine`
- `test_move_to_a_machine_known_to_lack_a_tool_is_refused_naming_it`
- `test_queued_move_is_refused_too`
- `test_worker_does_not_claim_a_card_its_profile_cannot_satisfy`
- `test_worker_claims_a_requires_kanban_card_without_kanban_in_its_chat_toolsets`
- `test_worker_defers_a_requires_kanban_card_when_kanban_is_disabled`
- `test_claim_refusal_reads_replica_row_fields`
- `test_request_changes_records_the_verdict_when_the_profile_here_lacks_a_requirement`
- `test_request_changes_tool_records_the_verdict`
- `test_request_changes_without_a_mismatch_records_no_warning`
- `test_request_changes_for_the_10_28_card_needs_no_warning`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

## Remote-assignee creation follow-up (1521)

### Behavior

When `runs_on` proves a Board-primary card cannot run on this machine, creation no longer rejects it
because the local same-named profile is missing or has different tools, skills or model configuration.
The landing machines' published capabilities still participate in create validation, and pickup
still checks the landing profile. Requirement shape/declaration and machine-independent explicit
model/provider compatibility remain checked. A concrete remote model pin must name its provider;
class selectors may retain automatic routing.

### How it works and Code

`hermes_cli/kanban_db_creation.py::validate_create_assignment` centralizes creation checks.
`kanban_runs_on_capabilities.runs_elsewhere` only skips local checks when routing excludes this
machine on a primary board. Missing/empty `runs_on`, non-primary boards and unresolved registry
state keep local checks; an explicit other-machine label needs no registry to exclude this machine.
`kanban_capabilities.validate_assignment(check_profile=False)` validates the declared shape without
loading local skills. `kanban_model_assignment` skips local profile configuration, and
`kanban_pin_provider.require_remote_pin_provider` refuses provider-less concrete remote pins.
`kanban_db.create_task` calls this combined helper. Landing workers retain their own registry
validation and missing-pinned-skill note behavior.

### Tests

Source `tests/hermes_cli/test_kanban_runs_on_remote_assignee.py` covers capable/known-incapable/unknown
remote tools, later pickup refusal, cards still eligible locally, malformed requirements, no local
skill/profile model lookup, provider-less pin refusal, and
`test_tool_create_with_placement_on_an_enrolled_machine_creates_the_card` through the tool entry.
Not executed in this documentation pass; CLI/REST remote-assignee parity is not newly certified.

### Applying and Gotchas

Use the complete ordered [README replay recipe](README.md#applying) through 1521. Unknown published
tools are not proof of capability: create may accept while the landing pickup gate refuses.
Skipping a local profile check neither grants credentials nor guarantees the remote skill exists.

## Follow-up patch (1558): remote-assignee entry paths and unknown labels

**Patch:** `patches/1558-daveey-Cover-remote-assignee-entry-paths-and-fail-sa.patch` (`9e70ec54cdf`, `Cover remote assignee entry paths and fail safe on unknown labels`).

**Behavior.** Repinning (`set_model_override` via `validate_task_pin`) now follows the same execution-host policy as create (1521): when the card's `runs_on` proves it runs elsewhere, the local profile's model configuration is skipped and a concrete pin must name its provider (`require_remote_pin_provider`); the explicit model/provider family check still applies. A card that runs locally keeps the full local validation, including the managed routing registry. `runs_elsewhere` now also keeps local checks when the registry returns no entry for this machine (empty registry or missing self entry), not only when it is unreachable: unknown labels are not evidence that this machine lacks them.

**How it works / Code.** `hermes_cli/kanban_model_assignment.py::validate_task_pin` reads the card row (tolerating boards without the `runs_on` column), calls `runs_elsewhere`, passes `local_profile=not remote` to `validate_model_assignment`, and returns the explicit pair for remote cards. `hermes_cli/kanban_runs_on_capabilities.py::runs_elsewhere` returns False when `pool.get(me)` is empty.

**Tests.** `tests/hermes_cli/test_kanban_remote_assignee_entry_paths.py`:

- `test_plain_label_remote_profile_creates` (plain `gpu` label resolved through the registry)
- `test_unknown_local_labels_keep_local_validation` (unreachable, empty, missing-self: create refused, no row)
- `test_heavy_route_remote_only_assignee` (tool create via heavy-route placement)
- `test_cli_runs_on_remote_only_assignee` (CLI `--runs-on`, remote creates, local exits 2)
- `test_set_model_override_uses_execution_host_policy` (remote repin accepted, family mismatch and provider-less pin refused, class selector and clear work; local repin refused by the registry)

REST (dashboard) create and REST repin are not covered here: untested. This mirror did not run these tests.

**Applying / Gotchas.** Apply the full ordered series per [README.md](README.md#applying); standalone application is not certified. A label that the registry cannot settle now always means "validate locally", so a card for a truly remote-only profile is refused at create until the registry lists this machine.

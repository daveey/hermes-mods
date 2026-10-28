# Kanban default placement: a card with no local-only need is created on the routed gateway

Patches **0565** (source `89b72a8684`), **0567** (`8d1ca16533`, `hub_cli_path()` fix-up), **0569** (`d57aebb0cb`,
configuration docs), **0572** (`5ac187baca`, carried options) and **0630** (`7f117fa465`, the hub task id comes back;
no duplicate on retry), **0642** (`ae139f8d31`, identity failures refuse explicit pins) and **0646**
(`4f6e223fe8`, title-only host markers, visible local decisions and creator-scoped archive). Companion: [kanban-capability-routing.md](kanban-capability-routing.md) (the route back).

## Behavior

A gateway whose own machine must stay quiet (a laptop the user works on) maps itself to a target gateway:

```yaml
kanban:
  placement:
    route: {macbook: hub}          # this gateway's hermes-hub name -> target
    creator_session: {hub: <session id>}   # optional, per target
```

`kanban_create` then creates the card on the target through
`hermes-hub create-task <gw> <title> --body ... --assignee ... --id <request id> --origin-session <chat> --origin-profile <p> --creator-session <kanban.placement.creator_session[target]>`.
The card shows in the chat that asked for it and runs (and burns CPU) on the target. The tool result carries
`placed: {gateway, request_id, task_id, origin_session, queued_only, retry}`. Since **0630** the tool waits up to
`kanban.placement.wait_seconds` (default 10 s) for the target to make the card and returns its id as the top-level
`task_id` with the target's `status` (`ready`...), the same shape as a local create. If the target has not made it
yet, the result is `ok: true, task_id: null, status: "queued"` and the note says the card IS created and that
calling `kanban_create` again creates a duplicate. An identical retry (same request id) is never sent again; its
result has `placed.retry: true` and returns the same card.

The card stays local (created here as before) when any of these holds:

- the caller passed `placement="local"` (new `placement` parameter on `kanban_create`; `placement=<target>` pins it remote and skips the marker check);
- it has a project, a `dir`/`worktree` workspace, a non-default board or a non-default assignee
  (`kanban.placement.assignees`, default `["default"]`); a card with parents is created here but, since
  **0695**, still runs on the target once its parents finish (see "Deferred placement" below), unless
  `kanban.placement.defer_parents: false` or another rule here keeps it local;
- it is courier creation for another gateway (`origin_intent`);
- it uses options `hermes-hub create-task` cannot carry: `initial_status`, `human_request`, `tenant` (and, with a
  hermes-hub client older than 1.8.0, `model`, `provider`, `goal_mode`, `triage` or a non-`local-only`
  `completion_contract`; see "Carried options" below);
- a `requires` need that this gateway holds is not held by the target, or it names a profile tool that only this gateway validates;
- its text matches a local marker regex (Chrome, CDP, Keychain, TCC, Apple apps, launchd, Desktop app/DOM,
  AWS SSO profiles...) or one of its skills matches a local skill marker. Since **0646**, built-in bare host-name
  markers (`MacBook`, `Mac mini`, `the mini` and the built-in machine-name spelling) scan only the title;
  mentioning a host in the body as a repro or verification step does not itself pin the card. Desktop DOM,
  CDP and Chrome markers still scan both title and body. `local_markers` replaces the built-in list and every
  replacement pattern scans all text; `extra_local_markers` extends the list, `local_skill_markers` replaces the skill list;
- the target is not live: this gateway's hermes-hub sync has not succeeded within `hub_live_seconds` (default 600, min 60)
  or the target's capability record is stale.

For automatic placement, if `hermes-hub` exits non-zero (other than 3) the card is created locally and a warning is logged.
An explicit foreign gateway pin instead fails with `placement_unavailable` and creates no local card. Exit code 3
means the hub was offline: the request is queued durably and sent on the next sync, so the card is not duplicated
locally (`queued_only: true`). An unset `route` (the default `placement: {}`) is exactly upstream behaviour.

**0642: explicit pins fail closed even if gateway identity lookup raises.** An explicit name other than
`auto`, `local` or `here` is refused, with a lowercased pin and `created: false`; no local row is written.
A name cannot be assumed to mean this gateway if identity lookup failed. If identity was successfully resolved
before a later exception, a pin matching that known own gateway still stays local. No pin, `auto`, `local`
and `here` retain the local fallback; courier decisions (`origin_intent`) remain local.

**0646: a marker-local create explains itself.** A successful automatic `kanban_create` kept local by a
marker returns `placement: {where: "local", why: "local marker: mentions 'macbook'"}` (with the actual reason)
and a separate top-level `remedy`: `if the work doesn't need this machine, archive and re-create with placement='hub'`.
Hub placement and structural local keeps do not get these marker fields. A worker may use `kanban_archive`
to archive an unclaimed card created by its own assigned task, then re-create it with `placement='hub'`.
The creator is checked from the card's created event against `HERMES_KANBAN_TASK`; claimed cards, foreign
cards and the worker's own assigned card are refused (as are done/archived cards for workers).
The same creator gate applies to ordinary CLI archive. Without `HERMES_KANBAN_TASK`, the existing
human/orchestrator archive behavior is unchanged. This is a narrow archive exception, not permission to
mutate arbitrary cards.

The placed card's body ends with a footer telling the target's worker to block with `needs=['host:<gw>']`
(or an exact capability such as `aws:<account>:admin`, `ssh:mini`) when it hits something only the originating
gateway has, so capability routing opens an owner card back there.

## How it works

- `hermes_cli/kanban_placement.py`: `decide(args, ...)` returns `{where, why}` (plus `pin` on refusal) and never raises;
  its exception path uses `_raw_explicit_pin()` to refuse unresolved explicit names, preserving local fallback
  only for unpinned/local choices, a known own-gateway pin, or courier creation (**0642**).
  `TITLE_ONLY_LOCAL_MARKERS` and `_local_marker(title, body, skills, cfg)` implement the host-name split;
  `LOCAL_MARKER_REMEDY` supplies the recovery text (**0646**).
  `create_remote()` runs the hub CLI through the injectable `hub_runner`; `request_id()` is stable per
  (gateway, idempotency key or title+body+assignee+origin+skills) so an exact retry reuses the hub request;
  `remote_task_id()` reads the hub's `sent` table; `target_live()` checks the hub `meta.last_ok` and the target's registry record.
- `tools/kanban_tools.py` `_place_remote()` is called from `kanban_create` when there is no `origin_intent` and no
  explicit origin. Since **0646**, it returns `(tool_result, None)` for remote results/refusals,
  `(None, marker_placement)` for a marker-local decision, or `(None, None)` for other local keeps.
  `_handle_create()` exposes the marker decision and top-level remedy after local creation.
- `hermes_cli/kanban.py::worker_may_archive()` shares the creator/claim gate between CLI `_cmd_archive()` and
  `tools/kanban_tools.py::_handle_archive()`. **0646** registers `kanban_archive` in the schemas and toolsets;
  `agent/prompt_builder.py` teaches workers the archive/re-create remedy.
- `tools/kanban_tools_schemas.py` adds the `placement` parameter; `hermes_cli/config_defaults.py` adds `kanban.placement: {}`.
- **0567** adds `hermes_cli/kanban_capability_registry.py::hub_cli_path(configured=None)`: configured value, then
  `PATH`, then `~/.local/bin/hermes-hub`, else the bare name. Gateway and worker processes (launchd, Hermes Python.app)
  lack the login shell's `PATH`, so a bare `hermes-hub` raised ENOENT and every cross-gateway create failed. Capability
  routing (`kanban_capability_routing._hub_cli`) and placement both use it.

- **0630** — why: a chat got `task_id: null` back, read it as "not created", called `kanban_create` again and
  made a stray card. The request result reached the MacBook only in its next full `hermes-hub sync` (~30 s on a busy
  Mac), and an origin-bound create stays `waiting` on the hub until that sync accepts the binding, so the 8 s poll of
  the local `sent` table never saw it. `kanban_placement.await_task_id()` now asks
  `hermes-hub outcomes --id <rid>` (client 1.9.1+, probed from `hermes-hub --help`) about once a second; that fetches
  finished outcomes and reads the hub's copy of the request, whose `waiting` result already carries the owner's
  task id. An older client gets ONE detached `hermes-hub sync --quiet` (it outlives the wait and records a late
  result) while the replica is polled. `create_remote()` skips `create-task` when the request id is already
  `queued/sent/done` locally. `remote_task_id()` also reads a plain (unbound) create's truncated `output` text.

## Code

`hermes_cli/kanban_placement.py` (new), `hermes_cli/kanban_capability_registry.py` (`hub_cli_path`),
`hermes_cli/kanban_capability_routing.py` (`_hub_cli`), `tools/kanban_tools.py` (`_place_remote` + call site),
`tools/kanban_tools_schemas.py`, `hermes_cli/config_defaults.py`, `website/docs/user-guide/configuration.md` (0569).
0646 also changes `hermes_cli/kanban.py`, `toolsets.py` and `agent/prompt_builder.py`.

## Tests

`tests/hermes_cli/test_kanban_placement.py` (exercises the `kanban_create` tool and `decide()`):

- a card with no local need goes to the target; no route is upstream behaviour;
- cards `hermes-hub` cannot express stay local (parametrized: parents, project, workspace, board, assignee, model, goal mode...);
- local markers and a local skill pin the card; configured markers replace and extend the built-in list;
- a caller pin wins both ways; courier creation is never re-placed;
- a declared need held here but not on the target stays local; one the target holds goes there;
- a non-live target falls back to local; `decide` never raises; `target_live` needs a recent sync and a fresh target record;
- a chat card is created on the hub bound to its chat; the hub task id is returned once applied; an unpersisted session creates an unbound hub card;
- a Mac-only card never calls the hub; a hub refusal falls back to a local card; an offline hub is queued, not duplicated; a retry reuses the request id;
- 0630 (each RED without the change): `test_task_id_comes_back_once_the_outcome_fetch_sees_the_hub_apply_it`,
  `test_task_id_comes_back_while_the_origin_binding_is_still_waiting`, `test_old_client_gets_one_detached_sync_and_the_task_id`,
  `test_still_queued_after_the_wait_says_the_card_is_created_and_not_to_call_again`,
  `test_identical_retry_returns_the_same_request_and_sends_no_second_card`, `test_remote_task_id_reads_a_plain_create_output`.
  The hermes-hub side (`outcomes`) is tested in the hermes-hub repo (`client/tests/test_outcomes_cli.py`). Live: a plain
  `hermes chat -q` that called `kanban_create` once got back hub task id `t_01a18119d1bf4d6e` in 12.5 s and left exactly
  one hub card.
- 0567: `test_hub_cli_resolves_without_the_login_shell_path` (PATH without the install dir, `~/.local/bin` fallback, configured value wins, routing uses it) and `test_remote_body_names_the_capability_to_route_back_with`.

- 0590–0591 (`fc317cf6f7`, `2ddf72b986`): `placement=<gateway>` that cannot be honoured returns `tool_error` code `placement_unavailable` and creates no card (never a silent local card). Tests: unrouted pin (`mini`, typos); no route configured (the hub-worker case, `placement='macbook'` on `hub`); hub refusal of a pinned card; structural limit and non-live target through the tool; exception while placing; `origin_session` set; `decide()` returns `refuse`. Controls: `placement` `local`, `here`, the own gateway name (any case, with or without a route) still create locally.

- **0642**, in the placement test file: `test_decide_refuses_explicit_pin_when_identity_lookup_raises`
  checks unresolved names versus no pin/auto/local/here and courier controls;
  `test_decide_keeps_a_known_own_gateway_local_when_a_later_step_raises` checks known-own versus foreign pins;
  `test_identity_lookup_failure_refuses_explicit_pin_and_creates_no_row` checks the tool error, lowercased pin,
  no hub call and no local row. `test_identity_lookup_failure_still_creates_local_and_here` and
  `test_identity_lookup_failure_without_a_pin_still_creates_locally` cover local tool controls.
- **0646**, in the placement test file: `test_marker_cases_a_through_e` covers a MacBook title staying local,
  a body-only verification mention going to hub, Desktop DOM/CDP/Chrome body markers staying local,
  the verbatim sidebar-projection regression fixture (`fixtures/t_01a180aae388e142_body.txt`) going to hub,
  and a local-only skill staying local. `test_local_marker_decision_is_in_the_create_result` checks the
  exact placement/remedy shape and its absence for hub and structural local results.
- **0646**, `tests/tools/test_kanban_creator_archive.py`:
  `test_worker_tool_archives_an_unclaimed_card_it_created`,
  `test_worker_tool_refuses_a_claimed_card_and_someone_elses`,
  `test_worker_cli_archives_only_an_unclaimed_card_it_created` and
  `test_orchestrator_cli_can_still_archive_any_card` cover tool/CLI creator cleanup and refusal controls,
  plus unchanged CLI behavior without a worker task. The added tests do not exercise self-archive refusal,
  done/archived worker refusal or the tool's no-worker control; those are source-level rules, not tested
  coverage claimed here. These test descriptions come from the commits; no tests were run for this doc update.

Untested: the CLI (`hermes kanban create`) and `execute_code` creation paths do not call placement (only the `kanban_create` tool does), and `test_kanban_create_entry_parity.py` has no placement case.

## Deferred placement for cards with parents (0695)

Why: parents are local links, so a card with parents was always created and run on the creating gateway. Every
orchestrator fan-out child (`parents=[self]`) and every follow-up of a laptop card ran on the laptop: on the
MacBook, 15 of the 175 cards created in the 24 h before the change stayed local only because of `parents`
(replayed through `decide()` with the parents dropped).

Behavior:

- `decide()` applies every other rule first. A card with parents and no other local-only need returns
  `{"where": "local", "defer": <target>, "why": "has parents: ..."}`. Target liveness is not checked then (the
  parents may run for hours), and an explicit `placement=<target>` with parents is deferred, not refused.
- `kanban_create` creates the card here (it waits in `todo` on its parents as usual), records a
  `placement_deferred` event `{target, origin_session, origin_profile}` and returns
  `placement: {where: "local", defer, why}` plus a note.
- When the parents finish and the dispatcher claims the card, `_default_spawn` runs
  `python -m hermes_cli.kanban_placement_forward -- <worker argv>` instead of the LLM worker
  (`forward_argv()`; review-lane spawns always get the real reviewer). The forwarder:
  1. creates the card on the target through `create_remote()` (same origin chat; options carried as before),
     with the parents' latest handoffs appended to the body and idempotency key `placement-forward:<gw>:<task>`,
     waits up to 45 s for the target's task id, comments on the local card, and parks it `scheduled` with
     `awaiting={kind: card, ref: <remote id>, gateway: <target>}` and wake probe
     `hermes kanban wait-check task <remote id> --gateway <target> --until done,archived`;
  2. when woken: remote `done` → completes the local card (summary points at the remote card), which releases
     local children; remote `archived` → blocks the local card `needs_input` and cancels the deferral (an unblock
     runs it here); otherwise parks again. A request still queued after the wait parks 10 min and waits for
     the same request, never sending a second card.
- Fallback: a target that is not live 3 times, a refusal, or an exception before the target accepted the card
  appends `placement_deferred_cancelled` and `exec`s the normal worker with the same claim, so the card runs here
  rather than stalling. After the target accepted it, an exception parks and retries instead (a local run would
  duplicate it).

Code: `hermes_cli/kanban_placement.py` (`_decide` defer branch, `decide` does not refuse a deferral),
`hermes_cli/kanban_placement_forward.py` (new: `record_deferral`, `state`, `forward_argv`, `Forwarder`, `main`),
`hermes_cli/kanban_db_dispatch.py` (`_default_spawn` hook), `tools/kanban_tools.py` (`_place_remote` defer result,
`_handle_create` records it).

Tests (`tests/hermes_cli/test_kanban_placement.py`, 105 pass; 17 RED with the source change stashed):
`test_child_with_parents_and_no_local_need_is_deferred_to_the_target`,
`test_child_with_parents_and_a_local_need_stays_local_and_is_not_deferred` (marker, skill, dir, board,
initial_status), `test_defer_parents_off_keeps_children_local`, `test_deferral_does_not_need_the_target_live_now`,
`test_pin_to_the_target_with_parents_is_deferred_not_refused`, `test_caller_pin_wins_both_ways` (updated: a hub pin
with parents is deferred), the tool path `test_kanban_create_with_parents_creates_a_local_deferred_card`, the forwarder
`test_forwarder_sends_the_card_with_parent_handoffs_and_parks_until_the_hub_card_is_done` (a local grandchild waits
until the remote card is done), `test_forwarder_falls_back_to_a_local_worker_when_the_hub_refuses`,
`test_forwarder_retries_a_target_that_is_not_live_then_runs_it_here`,
`test_forwarder_waits_for_a_queued_request_without_sending_it_twice`,
`test_hub_card_archived_blocks_the_local_card_for_a_decision`, `test_main_execs_the_worker_when_forwarding_falls_back`
and the dispatcher path `test_dispatcher_spawns_the_forwarder_for_a_deferred_card`. Untested: the real `hermes-hub`
binary and the ssh wait-check (both stubbed); the CLI `hermes kanban create` path does not defer (it never placed).

Gotchas: the local card stays open (`scheduled`) while the remote one runs, so the creating chat shows two rows
for the work: the local stub and the remote card. A `kanban.sync.pickup` worker node (see
[kanban-central-service.md](kanban-central-service.md)) could run such a card under a Board lease instead of a
forwarded copy; this patch does not depend on it.

## Carried options (0572)

hermes-hub 1.8.0 adds `create-task --option KEY=JSON`. When `hermes-hub create-task --help` lists `--option`,
placement passes `skills`, `max_runtime_seconds`, `priority`, `model`, `provider`, `goal_mode`, `goal_max_turns`,
`completion_contract`, `requires` and `triage` as card fields (`CARRIED_OPTIONS`; defaults such as `False`, `0`,
`local-only` are left out; `idempotency_key` is not sent because it already fixes the request id). So a model pin,
goal mode, triage or PR contract no longer pins the card local, and skills and the runtime budget no longer ride as
body text (`remote_body(..., carried=True)`). An older client keeps the previous rule: `model`, `provider`,
`goal_mode`, `triage` and a non-local contract (`LEGACY_UNCARRIED`) keep the card local and skills/runtime stay in
the body.

- `hub_carries_options(hub_cli)` probes `create-task --help` and caches the answer 10 min (`HELP_CACHE_SECONDS`);
  `decide()` probes only when a legacy-uncarried option is actually used (`carries=` can be injected).
- `request_id()` folds the carried options into the hash only when there are any, so a card without options keeps
  the id older clients computed (exact retries still dedupe).
- `hermes kanban create --reasoning-effort EFFORT` (parser, `_cmd_create`) is new; hermes-hub maps a plain create's
  reasoning_effort onto the hub option. `kanban_create` has no such parameter.
- The same commit fixes the BOM/FURB167 lint in `scripts/run_tests_host_slots.py` that kept `scripts/check` red.

Tests (`tests/hermes_cli/test_kanban_placement.py`, run on the mirror host: with `test_state_db_darwin_fd_scan_hang.py`
and `test_anthropic_pool_pin.py`, 68 passed): `test_options_a_new_client_carries_go_to_the_target`,
`test_options_no_client_carries_stay_local`, `test_decide_probes_the_client_only_when_a_legacy_option_is_used`,
`test_carried_options_are_json_typed_and_skip_defaults`, `test_hub_carries_options_reads_create_task_help_and_caches`,
`test_new_client_carries_skills_goal_mode_and_pins_as_fields`, `test_old_client_keeps_a_goal_card_local_and_skills_in_the_body`,
`test_kanban_cli_create_takes_reasoning_effort`. Untested: the real `hermes-hub` binary (the runner is stubbed).

Applying: `git am -3` 0572 after 0565–0569. Gotcha: a gateway whose hub client is older than 1.8.0 behaves as before
until the client self-updates (up to an hour plus the 10 min cache).

## Applying

`git am -3` 0565, then 0567 and 0569. Needs the capability-routing patches (0536–0540) and a hermes-hub CLI with `create-task --origin-session/--creator-session`.
0642 (`ae139f8d31`) extends the 0590–0591 explicit-pin refusal behavior; apply it before 0646
(`4f6e223fe8`), whose placement changes build on it.

## Gotchas

- Automatic placement only works while the hub sync is fresh; a laptop that was asleep creates unpinned cards
  locally until it syncs. An unavailable explicit foreign pin is refused, not silently created here.
- A marker false positive keeps a card local; pass `placement=<target>` to override. For an already-created
  unclaimed card, use the archive/re-create remedy above. `extra_local_markers` adds local matches; it does not
  remove a false positive. Replace `local_markers` to change the list, remembering replacements scan title and body.
- Bare host-name body mentions no longer pin by default; Desktop DOM/CDP/Chrome mentions still do.
- The target worker sees a normal card with the footer note; it has no dependency link back to the creating chat's local cards.
- Plugin? None; the route and markers are plain config in `shared-config.json`.
- The public patch 0565 has the private AWS profile names in its marker list and one test string replaced by `example-org` / `example-sso` / `--profile example` (a scrub, same line counts); set your own `extra_local_markers`.

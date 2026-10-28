# Kanban default placement: a card with no local-only need is created on the routed gateway

Patches **0565** (source `89b72a8684`), **0567** (`8d1ca16533`, `hub_cli_path()` fix-up) and **0569** (`d57aebb0cb`,
configuration docs). Companion: [kanban-capability-routing.md](kanban-capability-routing.md) (the route back).

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
`placed: {gateway, request_id, task_id, origin_session, queued_only}`; `task_id` is filled when the hub applies
the request within `kanban.placement.wait_seconds` (default 8 s).

The card stays local (created here as before) when any of these holds:

- the caller passed `placement="local"` (new `placement` parameter on `kanban_create`; `placement=<target>` pins it remote and skips the marker check);
- it has parents, a project, a `dir`/`worktree` workspace, a non-default board or a non-default assignee
  (`kanban.placement.assignees`, default `["default"]`);
- it is courier creation for another gateway (`origin_intent`);
- it uses options `hermes-hub create-task` cannot carry: `model`, `provider`, `goal_mode`, `triage`,
  `initial_status`, `human_request`, `tenant`, a non-`local-only` `completion_contract`;
- a `requires` need that this gateway holds is not held by the target, or it names a profile tool that only this gateway validates;
- its title/body matches a local marker regex (Chrome, Keychain, TCC, Apple apps, launchd, Desktop app, this Mac,
  the mini, AWS SSO profiles...) or one of its skills matches a local skill marker. `local_markers` replaces the
  built-in list, `extra_local_markers` extends it, `local_skill_markers` replaces the skill list;
- the target is not live: this gateway's hermes-hub sync has not succeeded within `hub_live_seconds` (default 600, min 60)
  or the target's capability record is stale.

If `hermes-hub` exits non-zero (other than 3) the card is created locally and a warning is logged. Exit code 3
means the hub was offline: the request is queued durably and sent on the next sync, so the card is not duplicated
locally (`queued_only: true`). An unset `route` (the default `placement: {}`) is exactly upstream behaviour.

The placed card's body ends with a footer telling the target's worker to block with `needs=['host:<gw>']`
(or an exact capability such as `aws:<account>:admin`, `ssh:mini`) when it hits something only the originating
gateway has, so capability routing opens an owner card back there.

## How it works

- `hermes_cli/kanban_placement.py`: `decide(args, ...)` returns `{where, why}` and never raises (any error means `local`);
  `create_remote()` runs the hub CLI through the injectable `hub_runner`; `request_id()` is stable per
  (gateway, idempotency key or title+body+assignee+origin+skills) so an exact retry reuses the hub request;
  `remote_task_id()` reads the hub's `sent` table; `target_live()` checks the hub `meta.last_ok` and the target's registry record.
- `tools/kanban_tools.py` `_place_remote()` is called from `kanban_create` when there is no `origin_intent` and no
  explicit origin; it returns the tool result, or `None` to fall through to local creation.
- `tools/kanban_tools_schemas.py` adds the `placement` parameter; `hermes_cli/config_defaults.py` adds `kanban.placement: {}`.
- **0567** adds `hermes_cli/kanban_capability_registry.py::hub_cli_path(configured=None)`: configured value, then
  `PATH`, then `~/.local/bin/hermes-hub`, else the bare name. Gateway and worker processes (launchd, Hermes Python.app)
  lack the login shell's `PATH`, so a bare `hermes-hub` raised ENOENT and every cross-gateway create failed. Capability
  routing (`kanban_capability_routing._hub_cli`) and placement both use it.

## Code

`hermes_cli/kanban_placement.py` (new), `hermes_cli/kanban_capability_registry.py` (`hub_cli_path`),
`hermes_cli/kanban_capability_routing.py` (`_hub_cli`), `tools/kanban_tools.py` (`_place_remote` + call site),
`tools/kanban_tools_schemas.py`, `hermes_cli/config_defaults.py`, `website/docs/user-guide/configuration.md` (0569).

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
- 0567: `test_hub_cli_resolves_without_the_login_shell_path` (PATH without the install dir, `~/.local/bin` fallback, configured value wins, routing uses it) and `test_remote_body_names_the_capability_to_route_back_with`.

Untested: the CLI (`hermes kanban create`) and `execute_code` creation paths do not call placement (only the `kanban_create` tool does), and `test_kanban_create_entry_parity.py` has no placement case.

## Applying

`git am -3` 0565, then 0567 and 0569. Needs the capability-routing patches (0536–0540) and a hermes-hub CLI with `create-task --origin-session/--creator-session`.

## Gotchas

- Placement only works while the hub sync is fresh; a laptop that was asleep creates cards locally until it syncs.
- A marker false positive keeps a card local; pass `placement=<target>` to override, or add `extra_local_markers`.
- The target worker sees a normal card with the footer note; it has no dependency link back to the creating chat's local cards.
- Plugin? None; the route and markers are plain config in `shared-config.json`.
- The public patch 0565 has the private AWS profile names in its marker list and one test string replaced by `example-org` / `example-sso` / `--profile example` (a scrub, same line counts); set your own `extra_local_markers`.

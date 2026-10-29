# fix(kanban): placement='hermes-hub' resolves to the 'hub' gateway instead of refusing the card [daveey]

Patch 0808, source `f88977402bf912bea0651ca05a2682214b055f2b`.

## Behavior

fix(kanban): placement='hermes-hub' resolves to the 'hub' gateway instead of refusing the card [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `answer (:func:await_task_id) and returns its task id like a local create. Stil`
- `def _uncarried(args: dict, c: _Cfg, carries: Optional[bool]) -> list[str]:`
- `def decide(args: dict, *, origin_intent=None, now: Optional[float] = None, cfg:`
- `def _decide(args: dict, *, origin_intent=None, now: Optional[float] = None, cfg:`
- `def test_dispatcher_spawns_the_forwarder_for_a_deferred_card(mac, monkeypatch):`
- `KANBAN_CREATE_SCHEMA = _schema(`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Models pin the hub by its host name (hermes-hub: EC2 host, hermes-hub client, docs, memory and
> the kanban_create schema's own example 'MacBook -> hermes-hub'), but the gateway is named 'hub',
> so decide() refused: "placement 'hermes-hub' is not a routed gateway (only 'hub')" and no card
> was created (6 sessions incl. 4 cron runs, 10-24..10-28).
>
> canonical_pin() resolves kanban.placement.aliases and 'hermes-<gw>' when <gw> is this gateway or a
> routed one, before the route/own-gateway checks; unknown names are still refused. Schema text now
> names 'hub' as the gateway.

## Code

- `hermes_cli/kanban_placement.py`
- `tools/kanban_tools_schemas.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_placement.py`

New or changed test definitions present in the source diff:

- `test_host_style_alias_of_the_routed_target_is_placed_there`
- `test_host_style_alias_of_own_gateway_means_local`
- `test_configured_alias_maps_any_name_and_unknown_names_still_refuse`
- `test_hermes_hub_pin_through_the_tool_creates_on_the_hub_not_refused`
- `test_hermes_hub_pin_on_the_hub_itself_creates_locally`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

# fix(kanban): a capability owner card is not a duplicate of the card it serves [daveey]

Patch 0926, source `abee965bfbc7c82b1b53e157a5290b3cf1d09131`.

## Behavior

fix(kanban): a capability owner card is not a duplicate of the card it serves [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `Refusals are typed (:class:CapabilityRouteError), and nothing is written:`
- `ROUTED_EVENT = "capability_routed"`
- `def _default_runner(argv: list[str]) -> tuple[int, str]:`
- `def route(conn, task_id: str, needs, *, reason: str, expected_run_id: Optional[i`
- `An open card matches when:`
- `def key_family(key: Optional[str]) -> Optional[str]:`
- `def find(*, title: str, body: str, target_board: str, session_id: Optional[str],`
- `def test_woken_card_needing_another_gateway_is_claimed_here_once_its_owner_is_do`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The owner title embeds the waiting card's title, so create_overlap on the
> holder matched the requester itself and wait-check exited 2. Skip that card
> (and sibling owner cards for it) in the overlap scan, and send distinct_from
> for the waiting card plus the cards it awaits. An older hermes-hub client
> that rejects the new option still gets the create.

## Code

- `hermes_cli/kanban_capability_routing.py`
- `hermes_cli/kanban_overlap.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_capability_routing.py`

New or changed test definitions present in the source diff:

- `test_overlap_skips_the_waiting_card_a_capability_owner_quotes`
- `test_old_create_task_client_still_queues_without_the_overlap_option`
- `test_capability_route_creates_owner_when_reason_duplicates_requester_title`
- `test_unrelated_title_twin_still_rejects_the_owner_create`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

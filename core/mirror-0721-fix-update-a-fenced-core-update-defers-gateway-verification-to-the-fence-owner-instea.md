# fix(update): a fenced core update defers gateway verification to the fence owner instead of owing a false restart [daveey]

Patch 0721, source `fc7b1ac399cda5d9ea62e0d78c73535c91dae05b`.

## Behavior

fix(update): a fenced core update defers gateway verification to the fence owner instead of owing a false restart [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _verify_fleet_after_update(restart, *, _pre_update_plan, _windows_gateway_re`
- `+"""[daveey] Is this update running inside a core-update launcher fence?`
- `+"""[daveey] A fenced core update must not report a false gateway_restart follow-up.`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> hermes-sync runs `hermes update --yes` inside its core-update fence: <home>/core-update/intent
> holds its pid and every hermes-gateway-launcher parks until it is released, which happens only
> after the update exits. The post-update fleet probe waited its 120 s settle window, saw no rows
> and recorded a gateway_restart follow-up on every fenced update (David-MBP: 5 consecutive runs,
> gateway back 7 s after each update exited).
>
> When an intent is held by a live ancestor of this process, probe once without the settle window;
> an empty fleet is then 'held by the fence', not failed. The fleet obligation stays armed and is not
> used for migration, so a gateway that never returns still warns at the next CLI start. Stale or
> down rows the probe can see, a missing fence, and a non-ancestor intent keep the fail-closed path.

## Code

- `hermes_cli/update_cmd_fleet_verify.py`
- `hermes_cli/update_gateway_fence.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_update_fleet_core_update_fence.py`

New or changed test definitions present in the source diff:

- `test_fenced_update_defers_verification_instead_of_owing_a_restart`
- `test_without_a_fence_an_absent_successor_still_owes_the_restart`
- `test_an_intent_from_a_non_ancestor_is_not_a_fence`
- `test_fence_still_flags_a_stale_gateway_it_can_see`
- `test_ancestor_fence_owner_reads_only_live_ancestor_intents`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

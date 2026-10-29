# fix(kanban): honest requires and hub profiles no longer pin MacBook cards local [daveey]

Patch 0835, source `9056be951d1a698a4af29418fc525d6a2ab379a2`.

## Behavior

fix(kanban): honest requires and hub profiles no longer pin MacBook cards local [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `DEFAULT_CONFIG = {`
- `def probe_specs(cfg: dict) -> list[tuple[str, object]]:`
- `def refresh(cfg: Optional[dict] = None, *, now: Optional[float] = None) -> dict:`
- `def remote_records(*, exclude: Optional[str] = None) -> dict[str, dict]:`
- `A card stays local when any of these holds:`
- `HUB_TIMEOUT = 60`
- `DEFAULT_LOCAL_MARKERS = (`
- `CARRIED_OPTIONS = ("skills", "max_runtime_seconds", "priority", "model", "provid`
- `class _Cfg:`
- `def _raw_explicit_pin(args: dict) -> str:`
- `def decide(args: dict, *, origin_intent=None, now: Optional[float] = None, cfg:`
- `def _decide(args: dict, *, origin_intent=None, now: Optional[float] = None, cfg:`
- `from hermes_cli import kanban_placement as kp`
- `def decide(args, **kw):`
- `def test_no_route_is_upstream_behaviour():`
- `def test_decide_never_raises():`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Since #166 every agent create declares requires, and placement kept any card with a
> profile-tool entry (terminal/file/git/kanban) local, so almost nothing reached the hub.
>
> - requires: terminal/file/git/kanban travel (kanban.placement.carried_requires) when the
>   hub client carries options; host_repo and unknown names stay local. When the target
>   publishes profile-tools:<profile>:<tool> in its capability record (new: every gateway's
>   registry refresh publishes its profiles' configured tools), a tool the assignee's profile
>   lacks keeps the card local.
> - assignees: a profile the target advertises in its hermes-hub gateway record is placed
>   there; only a name the target has no profile of stays local.
> - markers: myapp/baby bear only pins when the title names the product, not a skill,
>   repo or path name in the body (metta disk checks loading myapp-ship-and-ops);
>   macOS no longer matches CI runner labels or file names (macos-latest, *-macos.yml).
>
> Card t_01a184ccf2b568d1.

## Code

- `hermes_cli/config_defaults.py`
- `hermes_cli/kanban_capability_registry.py`
- `hermes_cli/kanban_placement.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_placement.py`

New or changed test definitions present in the source diff:

- `test_honest_requires_with_the_default_assignee_goes_to_the_target`
- `test_host_repo_and_unknown_requires_stay_local`
- `test_carried_requires_is_configurable`
- `test_requires_a_target_profile_lacks_stays_local`
- `test_assignee_the_target_has_a_profile_for_goes_there`
- `test_unknown_assignee_stays_local`
- `test_target_info_reads_the_gateway_record_and_fresh_profile_tools`
- `test_registry_publishes_each_profiles_tools`
- `test_babybear_skill_or_repo_in_the_body_does_not_pin_the_card`
- `test_macos_ci_runner_labels_do_not_pin_the_card`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

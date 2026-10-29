# fix(doctor): gateway-supervision checks ask only whether s6 is PID 1, never systemctl [daveey]

Patch 0803, source `4c4db9bb128a808515deb26925806d5f405a5a5d`.

## Behavior

fix(doctor): gateway-supervision checks ask only whether s6 is PID 1, never systemctl [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _check_s6_supervision(issues: list[str]) -> None:`
- `def _check_gateway_service_linger(issues: list[str]) -> None:`
- `+"""hermes doctor's gateway-supervision checks never call a service manager.`
- `def test_doctor_reports_the_single_host_gateway_not_per_profile_slots(served_hos`
- `def test_doctor_raises_an_issue_when_nothing_owns_the_gateway_role(served_host,`
- `def test_doctor_checks_host_unit_linger_under_a_served_profile(served_host, tmp_`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> _check_gateway_service_linger and _check_s6_supervision used detect_service_manager() to learn
> whether s6 was PID 1; in a container that falls through to supports_systemd_services() and runs
> systemctl --user is-system-running, so plain `hermes doctor` called a service manager
> (release run 40717630046, e2e-upgrade core, test_entrypoint_in_a_fresh_process[module-doctor]).
> Both now ask _s6_running() directly.
>
> Tests: tests/hermes_cli/test_doctor_supervision_no_service_probe.py (both red before); the
> multiplex reporting tests now stub the s6 probe instead of detect_service_manager.
>
> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `hermes_cli/doctor_platform.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_doctor_supervision_no_service_probe.py`
- `tests/hermes_cli/test_multiplex_host_topology_reporting.py`

New or changed test definitions present in the source diff:

- `test_linger_check_does_not_probe_systemd`
- `test_s6_check_does_not_probe_systemd`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

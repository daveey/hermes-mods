# refactor(kanban): replica record identity helpers move to kanban_session_activity_records (file cap)

Patch 0909, source `e54472005d1eb086f3cc0e9929512bacc967572b`.

## Behavior

refactor(kanban): replica record identity helpers move to kanban_session_activity_records (file cap). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `from __future__ import annotations`
- `def _owner_attests_terminal(task: dict[str, Any], gw: str, owner_profile: str |`
- `def _project_replica(path: Path, now: float, me: str, rows: list, info_rows: lis`
- `+"""Which replica record speaks for a card: legacy-alias folding and retiring rows a fresh replica proves moved.`

## Code

- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_activity_records.py`

## Tests

No test file changes in this source commit. Runtime behavior is untested by this mirror unless an executed check is named in [mirror-958-verification.md](mirror-958-verification.md); full-series application is a structural check, not a behavior test.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

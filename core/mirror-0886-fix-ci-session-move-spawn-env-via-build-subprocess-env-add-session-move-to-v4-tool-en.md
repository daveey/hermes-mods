# fix(ci): session_move spawn env via build_subprocess_env; add session_move to v4 tool_enabled_unused toolset enum

Patch 0886, source `4a79c9aff5053fa23927720e0a934e6cfdf45ced`.

## Behavior

fix(ci): session_move spawn env via build_subprocess_env; add session_move to v4 tool_enabled_unused toolset enum. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `"project",`
- `def spawn_after_turn_move(session_id: str, to: str, reason: str) -> int:`

## Code

- `hermes_cli/observability/schemas/hermes.shared_metrics.v4.schema.json`
- `hermes_cli/session_handover.py`

## Tests

No test file changes in this source commit. Runtime behavior is untested by this mirror unless an executed check is named in [mirror-958-verification.md](mirror-958-verification.md); full-series application is a structural check, not a behavior test.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

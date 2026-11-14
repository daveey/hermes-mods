# Portable worktree requirements and home-relative placement boundaries

Mirrored entries: 3854, 3862, 3868, 3881.

## Behavior

A portable worktree source can move to, and be picked up on, a worker without the `host_repo` capability. Retained worktrees and nonportable workspaces keep their host requirements.

Home-relative placement spans end only when the current shell makes a known directory change, not on a subshell, pipeline stage, prose mention or unknown directory rotation. An immediate change from the Hermes home into its code subtree is code work, not state-directory work. Tests pin canonical-home spelling and case-insensitive state-path hit naming.

## How it works

Move validation and claim validation both use `effective_requires`. Once `capability_kind` proves `worktree_source`, it strips the stored `host_repo` requirement because implicit and explicit copies in old stored requirements cannot be distinguished; SSH needs and runs_on labels remain.

`kanban_placement` narrows `_CHDIR` to command-position cd/pushd in this shell; cd -, popd and rotating/bare pushd keep a conservative home span. `_CODE_TREE` names hermes-agent/repos/bin and exempts the immediate two-step code-tree cd from `_STATE_DIR`. The local-work and state-path changes in this batch add tests only.

## Code

- `hermes_cli/kanban_move.py`
- `hermes_cli/kanban_runs_on_capabilities.py`
- `hermes_cli/kanban_source_move.py`
- `hermes_cli/kanban_placement.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/hermes_cli/test_kanban_source_move.py`: Checks a portable source moves without host_repo, retained custody refuses, effective requirements preserve SSH needs and nonportable requirements, and pickup uses the same portability rule.
- `tests/hermes_cli/test_local_work.py`: Asserts tilde expansion happens before cwd joining and relative bytes paths resolve against a bytes-compatible current directory.
- `tests/hermes_cli/test_kanban_state_path_home_forms.py`: Adds uppercase .HERMES/logs input and asserts its named hit is the home-relative path rather than the entire cd span.
- `tests/hermes_cli/test_kanban_placement_home_spellings.py`: Distinguishes this-shell directory changes from subshell/pipeline/prose/unknown changes, checks resulting local/worker placement, and retains a large no-match linear-time guard.
- `tests/hermes_cli/test_kanban_worker_placement.py`: Adds two-step Hermes-home then code-subtree cd examples to the established worker-placement cases.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

Portability drops even an explicit stored host_repo requirement; express genuine host needs separately. Placement is textual analysis and fails toward retaining a local pin when cwd is unknown. Uppercase hit naming does not make every artifact classifier case-insensitive. The mandatory hostname escape repair remains necessary after historical replay.

## Plugin?

No. Move/pickup parity and placement classifiers are shared core admission decisions.

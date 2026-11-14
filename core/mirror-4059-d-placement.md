# Shell-aware home-path placement regression coverage

Mirror snapshot 4059; covered ordinals: 3899, 3909, 3919, 3920.

## Behavior

- A `cd` or `pushd` with an operand after an output redirection is not mistaken for a home-directory transition.
- Home-relative state-path evidence survives legitimate redirects, multi-digit descriptors, multiple home spans, and uppercase path spellings.
- Uppercase `CD` in prose is not a shell directory change. Hub-read root stripping treats only backslash-LF, not backslash-CR-LF, as line continuation.

## How it works

`hermes_cli/kanban_placement.py` introduces `_REDIR` and changes `_CMD_END` to skip complete redirects and their targets before requiring a real command end. This preserves bare `cd` with redirects while rejecting the same prefix followed by a directory operand. The remaining patches add tests around per-hit span extraction and the pool's use of all home-relative artifacts, without changing those implementations.

## Code

- `hermes_cli/kanban_placement.py`

### Mirrored commits

- 3899: `9edbaace2441c7227e09e5d1f11c17a9ac34be28` — Rejects false cd-home matches with operands after redirections.
- 3909: `f8716be6da613f3b7ce996dba443ab7a6af92b71` — Pins placement descriptor digits, path offsets, and independent spans.
- 3919: `42cb09e39be3f88abd86d9ee65f8e666de317409` — Pins backslash-CR as distinct from shell line continuation.
- 3920: `c95a58bcdc79342699e37926940daddb200aaaa5` — Pins uppercase CD prose and all home-relative pool artifact hits.

## Tests

- `tests/hermes_cli/test_kanban_state_path_home_forms.py`: operand-after-redirect negatives and redirect-only positives distinguish repo paths from home state; additional assertions pin multi-digit descriptors, case-insensitive `.hermes` matching, last relevant path selection, mid-text offsets, and separate verdicts for two spans.
- `tests/hermes_cli/test_kanban_hub_read_roots.py`: direct `without_read_paths` calls distinguish backslash-CR-LF from backslash-LF; the higher-level normalization would otherwise mask that distinction.
- `tests/hermes_cli/test_kanban_placement_home_spellings.py`: uppercase prose neither starts nor terminates lowercase shell home spans.
- `tests/hermes_cli/test_kanban_pool_queue.py`: the worker retains a card whose second home-relative hit names an artifact actually present on that worker, and records that artifact's own normalized path.

Tests were not rerun by this documentation pass. These descriptions report assertions inspected in the staged mail diffs, not fresh passing results. Whole-series replay and tree verification belong to the parent mirror verification; no deployment or live behavior is claimed here.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including its pinned historical replay bridges, snapshot policy/replay plan, and mandatory post-replay hostname escape repair and repaired-tree verification. These ordinals are not independent cherry-picks: prior schema, helpers, and shared-file context are dependencies. Do not replay only this document's subset or rewrite historical mails to avoid the repair.

## Gotchas

This is a placement heuristic, not a general shell parser. Quoted redirect targets matter, and a directory word such as `~2` must not be reduced to home. Historical hostname normalization can affect placement regexes: the mandatory post-replay escape repair in README Applying remains required.

## Plugin?

No. The core placement classifier and worker retention path must agree on every hit.

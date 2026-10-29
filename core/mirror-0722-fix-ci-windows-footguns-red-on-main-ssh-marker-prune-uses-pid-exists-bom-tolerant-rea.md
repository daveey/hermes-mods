# fix(ci): [daveey] Windows footguns red on main: SSH marker prune uses _pid_exists, BOM-tolerant reads, explicit ps encoding

Patch 0722, source `39b6ddb29fe4e913331292faae096825545fc60f`.

## Behavior

fix(ci): [daveey] Windows footguns red on main: SSH marker prune uses _pid_exists, BOM-tolerant reads, explicit ps encoding. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _ancestor_pids() -> set[int]:`
- `_SSH_RUNTIME_ATEXIT_ARMED = False`
- `def _prune_stale_ssh_runtime_markers(purelib: str) -> None:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> e4222cf97b (os.kill(pid, 0) and a utf-8 read in web_server.py) and fc7b1ac399 (text=True without
> encoding, utf-8 json read in update_gateway_fence.py) turned 'Windows footguns (blocking)' red on main
> and every PR. gateway.status._pid_exists keeps the prune contract: a dead pid's marker is removed, a
> live or permission-denied one is kept.

## Code

- `hermes_cli/update_gateway_fence.py`
- `hermes_cli/web_server.py`

## Tests

No test file changes in this source commit. Runtime behavior is untested by this mirror unless an executed check is named in [mirror-958-verification.md](mirror-958-verification.md); full-series application is a structural check, not a behavior test.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

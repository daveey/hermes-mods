# fix(update): a git ancestry timeout on a loaded host keeps HEAD instead of failing the stable lookup

Patch 0898, source `fb393b96b367b6a224abb547714e2f7df623403e`.

## Behavior

fix(update): a git ancestry timeout on a loaded host keeps HEAD instead of failing the stable lookup. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def resolve_source_target(channel: str, git_cmd=None, cwd=None, *, repository=No`
- `def _head_containing(git_cmd, cwd, commit: str, repository: str) -> str | None:`
- `def test_full_history_behind_release_is_proof(history, tmp_path, monkeypatch):`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> hermes update --check failed in the pm e2e shard (run 40785922193) with
> 'git merge-base --is-ancestor ... timed out after 10 seconds'. _head_containing's
> contract is that an unknown relation keeps HEAD; a timeout is unknown. Also give local
> git 60s like the neighbouring resolver.

## Code

- `hermes_cli/source_releases.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_stable_forward_only_shallow.py`

New or changed test definitions present in the source diff:

- `test_ancestry_check_timeout_is_an_unknown_relation_that_keeps_head`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.

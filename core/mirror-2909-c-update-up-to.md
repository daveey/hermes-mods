# Update main only as far as a selected first-parent commit

Patches: **2879**.

## Behavior

`hermes update --yes --up-to <sha>` can apply an already prepared mainline build while newer main commits are still building. It fast-forwards branch main to the selected full lowercase 40-hex commit rather than detaching or resetting.

## How it works

**2879:** after normal fetch and before a tree write/commit point, require branch main, a resolvable commit, HEAD as a proper ancestor, and membership in `rev-list --first-parent HEAD..origin/main`. Refuse incompatible target/read-only flags and ZIP/release/image/package-managed installs with exit 1 and `up_to_refused`. Pin preflight, commit point, interrupted-pull marker, expected receipt SHA and restart work to that target. Skip fork upstream sync/push for this run. The parser contains the literal capability marker `"--up-to"`. Move venv-holder printing to its Windows helper without behavior change.

## Code

- `hermes_cli/main.py`
- `hermes_cli/subcommands/update.py`
- `hermes_cli/update_cmd.py`
- `hermes_cli/update_cmd_windows.py`
- `hermes_cli/update_up_to.py`
- `website/docs/getting-started/updating.md`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_update_up_to.py` builds real Git histories with side branches/merge commits, drives update command setup, checks exact-target markers and receipts, subsequent plain update, unsupported flags/install kinds, unresolved commits and failed-ff no-reset/no-detach behavior.

- `tests/hermes_cli/test_update_up_to.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

The SHA is not normalized from an abbreviation or uppercase token. Already-at-target is refused. This is not permission to skip build verification or apply a side-branch commit. The next ordinary update still targets the then-current main tip.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.

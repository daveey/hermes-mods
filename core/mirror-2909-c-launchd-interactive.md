# Gateway launchd jobs use Interactive process type

Patches: **2869**.

## Behavior

The generated macOS gateway plist declares `ProcessType=Interactive`, aligning scheduling intent with the Desktop-facing backend rather than leaving launchd’s default Standard process type.

## How it works

**2869:** add the key to `generate_launchd_plist`. Existing currentness/rewrite machinery recognizes an installed plist missing the key as stale and rewrites it.

## Code

- `hermes_cli/gateway_launchd.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_launchd_process_type.py` parses the generated plist and checks a stale installed plist becomes current after rewriting.

- `tests/hermes_cli/test_launchd_process_type.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

File-generation/rewrite tests do not prove a loaded launchd job has adopted the key or establish live scheduler priority. Activation remains separate from editing a plist.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.

# Bounded always-on dashboard boot prewarm gate

Patches: **2857, 2894, 2895, 2896**.

## Behavior

The always-on dashboard waits for its boot prewarms before lifespan startup completes and the socket binds, subject to a shared wait cap. Ordinary Desktop-owned isolated backends do not wait. Logs name unfinished prewarms without mismatching count and labels.

## How it works

- **2857:** annotation/project-tree and board starters expose their boot threads; the end of `standalone_lifespan` awaits `hold_bind_for_prewarm` off the event loop. Threads share one six-second budget; a failed gate degrades to binding.
- **2894:** cap that wait at `max(0, min(6, 17 - process_age))` for a plausible boot age. Unknown/implausible ages fall back to six seconds. Annotation exposes its current phase, so logs distinguish session annotation from project tree.
- **2895:** compute the logged running count and names from one post-join liveness pass. Correct the explanation of Desktop’s attach window.
- **2896:** align the module docstring with the capped wait and process-start deadline.

## Code

- `hermes_cli/boot_warmup_gate.py`
- `hermes_cli/kanban_annotation_prewarm.py`
- `hermes_cli/web_server_app.py`
- `plugins/kanban/dashboard/board_prewarm.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_boot_warmup_gate.py` covers shared-budget joins, always-on vs owned-serve lifespan behavior, failed/missing prewarms, recorded threads, process-age limits, a slow boot, phase labels and thread completion between join and naming.

- `tests/hermes_cli/test_boot_warmup_gate.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

The 17-second deadline bounds additional gate waiting, not an already slow boot or the whole supervisor restart. Desktop’s attach timer begins after detection and SSH preflight; this patch does not prove a universal 20-second reconnect guarantee. Explicit test budgets bypass the age-derived default.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.

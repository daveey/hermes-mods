# Native macOS memory tests participate in the required CI gate

Patches: **2858, 2877**.

## Behavior

When CI selects the Darwin native-memory lane, “All required checks pass (v2)” requires its success. A skipped, failed, cancelled or absent selected lane cannot produce a green aggregate, including fork PRs that cannot run on the self-hosted macOS runner.

## How it works

- **2858:** add exact six-file `NATIVE_MEMORY_PATHS` classification, fail-open selection for empty/capped diffs, action/detect output and aggregate result requirements. CI calls `native-memory-macos.yml` as a reusable workflow with a pinned commit input; remove its independent PR trigger. Standalone main pushes remain, so CI masks its duplicate on non-release main pushes. Caller-specific concurrency avoids superseding required runs.
- **2877:** correct the CI header comment to name both macOS workflows retaining standalone push triggers.

## Code

- `.github/actions/detect-changes/action.yml`
- `.github/workflows/README.md`
- `.github/workflows/ci.yaml`
- `.github/workflows/native-memory-macos.yml`
- `scripts/ci/classify_changes.py`
- `scripts/ci/required_results.py`

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_native_memory_gate.py` composes classifier → detect → reusable caller → real aggregate evaluator, including path selection, fork skips, failure statuses and concurrency. `test_classify_changes.py` and `test_native_memory_workflow.py` check wiring/path parity. These are workflow tests; actual Darwin ABI/no-fork execution belongs to the native macOS lane.

- `tests/ci/test_classify_changes.py`
- `tests/ci/test_native_memory_gate.py`
- `tests/ci/test_native_memory_workflow.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

A generic `.github/` change does not itself select this exact-path lane. Linux tests cannot establish Darwin no-subprocess behavior. A fork touching these paths is deliberately not allowed to pass via a self-hosted skip.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.

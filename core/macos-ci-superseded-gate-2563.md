# macOS state gate delegates to a superseding duplicate run

Patches: **2508, 2509–2511**.

## Behavior

The separately required `macOS state required (v1)` CI job used to fail whenever the selected macOS state lane was `cancelled`, even when the cancellation came from a newer duplicate run of the same pull request commit that carries the verdict. Now, for a non-release `pull_request` run, a cancelled selected lane passes the gate if a successor duplicate run is found; the log prints `Cancelled duplicate run: run <id> on the same head SHA carries the required verdict`. Every other case behaves as before:

- Release runs never delegate (`--macos-state --release`; combining `--release` with `--allow-superseded` is a usage error).
- `push`, `workflow_dispatch` and other events never delegate and never query the API.
- Only a `cancelled` lane delegates, and only when the `detect` job succeeded with a known selection (`true` or `false`). Skipped, missing, failed lanes or unknown/empty selection fail as before.
- If the run lookup errors or returns nothing usable, the gate keeps the failure (`Superseding-run lookup failed, keeping the failure`).

The gate job also gained `contents: read` and `actions: read` permissions so the lookup can read workflow runs on a private repository.

## How it works

`.github/workflows/ci.yaml` builds the arguments for `scripts/ci/required_results.py`: `--macos-state` plus `--release` when `inputs.release` is true, else `--allow-superseded` when the event is `pull_request`; `GH_TOKEN` is passed from the job token. In `main`, the `--macos-state` branch repairs only the cancellation (substituting `success` for the lane) and re-evaluates; delegation happens only if that repaired verdict is OK and `allow_superseded` is set. The duplicate lookup is the new shared helper `delegated_run`, which wraps `superseding_run_id` (same fail-closed handling) and is now also used by the general required-results gate. The successor rules (same head SHA, workflow, event, branch and head repository, newer, not itself completed unsuccessfully or cancelled, rerun-attempt limits) are those of the existing superseded-run logic; see [ci-macos-state-required.md](ci-macos-state-required.md) for the gate itself.

## Code

`.github/workflows/ci.yaml`, `scripts/ci/required_results.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests):

- `tests/ci/test_macos_state_superseded.py`: the `required_results.main` CLI entry (with fake GitHub fetchers) across detect/selection/lane combinations, non-successor runs, rerun limits, lookup errors, no opt-in and `--release`; also the workflow's shell argument construction per event/release value, and the job's `permissions`. The workflow replay is isolated from a live Actions context and drains stdin before recording arguments.
- `tests/ci/test_required_results.py`: a cancelled selected lane delegating for pending and successfully completed successors.
- `tests/ci/test_macos_state_routing.py`: the policy replay no longer reads the host's `GITHUB_REPOSITORY`.

Real GitHub Actions execution of the gate is untested here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Delegation needs the job `actions: read` permission; without it the lookup fails and the gate stays red.
- It trusts the successor run to carry the required verdict; the gate itself does not wait for that run.

## Plugin

Core change; no plugin required.

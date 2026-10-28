# Git-consumer fallback test boundaries

Patch 0662 (`c70f80b17d`), following 0638 (`b98c132d9d`). Test-only; production behavior is unchanged.

## Behavior

Checkpoint and working-diff tests inject `RuntimeError` and `pm.InstallError` at the public `pm.ensure` boundary. Both consumers must still use real system Git, and the injected callback must be reached. The selected-environment wrapper test remains intact. Separate tests retain the real PM client and exercise a missing packaged runtime through each consumer.

## How it works

Consumer tests patch the facade read by `selected_git_env`, not private PM target/acquisition hooks. A disposable Docker install stamp points at a missing runtime. The runtime-state cases first assert the real preflight error, then verify public-boundary injection; the separate runtime-fallback tests leave `pm.ensure` unpatched throughout the consumer call.

## Evidence and why-chain

Problem: the earlier mirror run reported four successful consumers with `calls == []`. Clean latest main and a reconstructed historical applied range both passed the original five tests during this investigation; the exact earlier host state is unavailable.

Why 1: an earlier PM refusal can bypass the private hooks while `selected_git_env` correctly falls back — reproduced by a missing packaged runtime, with four callback failures before this fix.

Why 2: `pm.client.ensure` calls `is_runtime` before target-gap checks or acquisition. `pm.runtime._resident_runtime` raises `InstallError` when the packaged runtime is missing/invalid.

Root: consumer contract tests depended on PM implementation internals and runtime readiness rather than the consumer's public boundary.

Why not caught: a clean source checkout does not have the missing-runtime install stamp. Patch 0638 addressed an earlier target-gap refusal, but not runtime discovery preceding it. The new runtime-state parameter reproduces this gap; dedicated unmocked tests cover the real preflight-to-fallback path.

Evidence boundary: the missing-runtime mechanism is proven, but is not asserted to be the uniquely established cause of the original mirror incident.

## Code

`tests/tools/test_git_consumer_environment.py`; `tests/tools/test_git_consumer_runtime_fallback.py`. Relevant unchanged production paths: `hermes_cli/_subprocess_compat.py::selected_git_env`, `pm/client.py::ensure`, `pm/runtime.py::_resident_runtime`.

## Tests

Canonical `scripts/run_tests.sh`, using an existing PM-built test interpreter on Linux:

- New runtime-state parameter against old hooks: 5 passed, 4 failed at `assert calls` (RED).
- `tests/tools/test_git_consumer_environment.py`: 9 passed, original callback/consumer assertions preserved.
- `tests/tools/test_git_consumer_runtime_fallback.py`: 2 passed, real `pm.ensure` through both consumers.
- `tests/pm/test_client_target_gap.py`: 6 passed, real PM target-gap behavior remains covered separately.
- `tests/tools/test_checkpoint_manager.py`: 63 passed.
- `tests/tools/test_working_diff.py`: 6 passed.

The final post-rebase run passed all 86 tests. Applying public patches 0659–0662 with `git am -3` at `a9ed7fc9ff` succeeded; the applied public tree then passed the 17 consumer/runtime/PM-target tests. No full-repository or Windows run is claimed.

## Applying

Apply in curated series order, after 0661. The follow-up is append-only; do not replace 0638 or duplicate the source commit.

## Gotchas

The install-stamp fixture is for source-checkout tests under the canonical test interpreter, not execution inside the PM runtime or a Desktop payload. Never remove the callback assertion to accommodate an earlier refusal. Fallback success alone does not prove an injected failure was exercised.

## plugin?

No; repository regression tests only.

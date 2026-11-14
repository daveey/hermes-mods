# Scheduled macOS test lane yields to waiting runner work

Mirrored entries: 3857, 3884.

## Behavior

A non-required macOS OS-marked lane runs twice daily, on manual dispatch or a dedicated probe branch; it is not a pull-request lane or part of required aggregate CI. Scheduled runs skip when other work waits for the matching self-hosted runner. Manual/probe runs are not gated.

The queue gate reads successive pages of each open run state and treats the page cap as saturation, instead of overlooking a waiting job after the first page.

## How it works

The workflow’s Linux gate emits a run=true/false output; the macOS job depends on it, uses the self-hosted macOS/hermes-state labels, creates an isolated Hermes home, selects macos/posix-marked files and runs the non-integration selection at utility QoS with two workers. It requires a nonempty file list and does not cancel an already running lane.

`macos_os_tests_gate` deduplicates open run ids, excludes its own run, reads job lists in an eight-worker pool, and checks queued/waiting/pending/requested jobs for the runner label. An already running job alone does not skip the slot. Run listings page in 100-row chunks up to ten full pages per state, then skip as saturated. Ordinary API/parse failures fail open to testing, unlike intentional saturation.

## Code

- `.github/workflows/README.md`
- `.github/workflows/ci.yaml`
- `.github/workflows/macos-os-tests.yml`
- `scripts/ci/macos_os_tests_gate.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/ci/test_macos_os_tests_workflow.py`: Pins triggers, schedule, permissions, nonrequired wiring, runner labels and exact test-selection command. Tests waiting statuses, other/running jobs, own-run exclusion, deduplication, manual/probe bypass, unreadable-queue fail-open and GITHUB_OUTPUT. Follow-up fixtures put a waiting job on page two and fill every page to assert skip and bounded scanning.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

This is opportunistic scheduled coverage, not a guarantee of macOS testing before merge. A red scheduled run remains an alert; changing its identity requires coordination with any external watcher. The run-list pagination fix does not add job-list pagination beyond the existing per-run request. No real macOS lane was dispatched by this documentation pass.

## Plugin?

No runtime plugin. The workflow and its standalone CI gate belong to source CI configuration.

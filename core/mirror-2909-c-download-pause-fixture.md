# Deterministic partial-transfer pause regression

Patches: **2850**.

## Behavior

Test-only: a browsed local-model download pause/resume test observes a genuinely incomplete PM range transfer even when progress callbacks are coalesced on a throttled runner.

## How it works

**2850:** the loopback range server can withhold pieces beyond `hold_from` on an event. The test holds the final payload piece until the pause request is sent; teardown releases the server in all cases. Poll/callback deadlines become 60 seconds and return immediately when their conditions hold.

## Code

Test-only changes; no production implementation is modified.

## Tests

Source coverage listed below; **feature suites were not executed by the mirror pass**. Patch replay/tree verification is separate and does not establish these behavioral tests passed.

`test_browsed_download_can_pause_and_resume_pm_transfer` exercises the test client’s download-browsed, pause and job-poll endpoints with PM’s real downloader and loopback range server.

- `tests/hermes_cli/test_local_download_jobs.py`
- `tests/pm/_range_server.py`

## Applying

Use the [full ordered replay procedure](README.md#applying), including its integration bridges and required post-replay overlay. These feature patches share dependencies with the surrounding core series; do not apply this list as an independent cherry-pick recipe.

## Gotchas

No downloader product behavior changes. Increasing deadlines alone would not establish a partial transfer; the server-side hold is the ordering proof.

## Plugin status

Core/source-series change; no separately installable hermes-mods plugin is required. Any bundled plugin path above belongs to the core source tree, not an independently deployed mod.

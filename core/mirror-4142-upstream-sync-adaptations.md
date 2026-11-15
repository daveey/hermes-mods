# Upstream sync ae1ff71c39 side-branch adaptations

Mirror snapshot 4142; ordinals 4114–4118.

## Behavior

Five small fork-side adaptations that were needed after the fork's upstream-sync side branch took in upstream changes up to `ae1ff71c39`. They change no new user-facing feature. What they do:

- A mid-stream delivery e2e cell keeps its streamed footer visible again (4114).
- The self-update module preload list includes `hermes_cli.kanban_comment_writer`, so a lazily imported module is already loaded when the checkout is moved (4115).
- `gateway/AGENTS.md` is trimmed so the AGENTS.md chain stays under its 30000-character cap (4116).
- A boot-recovery test pins that recovered-source resolution runs off the gateway event loop (4117).
- A pending-route boot-recovery fixture is adapted to upstream's provenance recovery (4118).

## How it works

The series places these five commits at the integration point where the side branch was merged into main (integration merge `a3fe7bf2c7`): after the mainline entries 4060–4113 and before the post-merge entries 4119 and later. The integration merge itself had no hand resolution; merge-tree reproduces its tree.

Two hash-pinned bridges are applied around them:

- [`bridges/upstream-491f984583-agents-md-via-e4450f549b.diff`](bridges/upstream-491f984583-agents-md-via-e4450f549b.diff) before 4116. It supplies `gateway/AGENTS.md` as of the side-branch merge `e4450f549b` of upstream `491f984583`; 4116 then trims that file.
- [`bridges/upstream-ae1ff71c39-via-a3fe7bf2c7.diff`](bridges/upstream-ae1ff71c39-via-a3fe7bf2c7.diff) before 4118. It covers 304 paths: 302 paths upstream changed between `b51a30c461` and `ae1ff71c39`, plus the side branch's fork-side merge resolution in `hermes_cli/kanban_admission.py` and `hermes_cli/kanban_gateway_drain.py`, which thread upstream's `expect_assignee` claim compare-and-swap through the fork's claim helpers.

Per ordinal:

- 4114: in `test_crash_between_completion_and_send`, for the `stream_accepted_unpersisted` point, the answer text gets a trailing newline before being passed to `StallMidStream`. Upstream now withholds a still-arriving trailing token from mid-stream frames, so without the newline the footer only appeared in the final edit and the cell failed at a different point. With the newline the preview shows the complete answer while the stream is open and the cell returns to its known-gap xfail.
- 4115: adds `"hermes_cli.kanban_comment_writer"` to `POST_MOVE_MODULES` in `hermes_cli/update_cmd_commit.py`. Upstream's new `restart_child_env` case drives `served_profile_child_env` after the checkout moves; the fork's `_finalize_child_env` imports that module lazily (via `tools.environments.local`, which stamps the ssh agent marker), so it must be preloaded like `tools.environments.local`.
- 4116: in `gateway/AGENTS.md`, shortens the control-socket peer check paragraph (the 30 s cache of a passing "no symlinked ancestor" verdict) to a pointer into `gateway-internals.md`, and shortens one row of the profile-scope table (restored or hand-built sessions with no live provenance).
- 4117: in `test_native_recovery_resolves_route_owners_off_the_loop`, monkeypatches `gateway.session_native_recovery_routes.recovered_native_source` with a recording stub and expects bindings with a `:recovered` suffix, plus `recovered_source` among the recorded calls, none on the loop thread.
- 4118: in `test_native_recovery_binds_only_routes_with_pending_owners`, stubs `recovered_native_source` to return `entry.origin`, so the test exercises route selection only.

## Code

Source paths touched by this feature group (including regression fixtures):

- `tests/e2e/core/delivery/test_messaging_exactly_once.py`
- `hermes_cli/update_cmd_commit.py`
- `gateway/AGENTS.md`
- `tests/gateway/test_boot_recovery_off_loop.py`

Mail entries in the ordered series:

- 4114: [`7e69a7e684ff091bef4f6b19d69720e9e14869d5`](patches/4114-test-e2e-settle-the-streamed-footer-before-the-strea.patch)
- 4115: [`34172c68f73619c212bb5b0fd7e39e93a311871b`](patches/4115-daveey-update-preload-kanban_comment_writer-before-t.patch)
- 4116: [`e1543ce4b1b14ce6731b2cbd75be19d57ef78a5c`](patches/4116-daveey-gateway-AGENTS.md-stay-under-the-30000-char-c.patch)
- 4117: [`3e46d5051d34181e6cae7d7ebdbcf2cbc3784996`](patches/4117-daveey-Pin-recovered-source-resolution-off-the-gatew.patch)
- 4118: [`4a2a8807ebf472fd536197a33b0c2014c3e71bb5`](patches/4118-daveey-Adapt-pending-route-fixture-to-upstream-prove.patch)

## Tests

- `tests/e2e/core/delivery/test_messaging_exactly_once.py::test_crash_between_completion_and_send` (changed, 4114): for the `stream_accepted_unpersisted` point the stalled stream text ends in a newline so the footer is settled mid-stream.
- `tests/gateway/test_boot_recovery_off_loop.py::test_native_recovery_resolves_route_owners_off_the_loop` (changed, 4117): `recovered_native_source` is called with the authority and owner `root`, bindings use the recovered source, and no recorded call ran on the loop thread.
- `tests/gateway/test_boot_recovery_off_loop.py::test_native_recovery_binds_only_routes_with_pending_owners` (changed, 4118): provenance lookup is stubbed; only routes with pending owners are bound.
- 4115 (`POST_MOVE_MODULES` entry) and 4116 (documentation file) add no test of their own.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- The two bridge diffs are hash-pinned to the named upstream and merge commits; they must be applied at the positions given above (before 4116 and before 4118), not at the end.
- 4116 only makes sense on top of the first bridge's `gateway/AGENTS.md`; the trimmed text relies on the chain cap of 30000 characters.
- 4117 and 4118 assume the upstream `recovered_native_source` function in `gateway.session_native_recovery_routes`. The series delivers it with the second bridge, which follows 4117, so the intermediate tree right after 4117 is not test-certified; only the final tree is verified.
- The e2e cell in 4114 stays a known-gap xfail; the change restores where it fails, it does not fix the gap.
- This document makes no deployment claim.

## Plugin?

No. These are adaptations of core files, a core module preload list and core test fixtures to an upstream sync; a plugin cannot replace them.

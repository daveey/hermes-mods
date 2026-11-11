# Hash-pinned carried inputs do not force host-only placement

Patches **2206–2208**.

## Behavior

A card can remain portable when its only host-state marker is an exact frozen input file verified by a fresh carriage receipt. The card must include that file's matching lowercase SHA-256, the local file must still match, and the receipt must cover every configured worker-pool and heavy-route destination. This is not a blanket exemption for evidence directories or workspaces.

BOM-prefixed UTF-8 receipt JSON is accepted. Missing, stale, future-dated, malformed or insufficient receipts leave the ordinary host-marker checks in force. Explicit local markers are checked before this exemption.

## How it works

`without_carried_inputs` reads `$HERMES_HOME/kanban/carried-evidence.json` as `utf-8-sig`. Version 1 receipts contain `verified_at`, a `machines` list and a `files` mapping from exact path tokens to digests. Receipt age must be between zero and 3600 seconds. Both receipt and local input are limited to 1 MiB; symlink receipts, symlink/noncanonical input paths and parent traversal are not accepted.

Only matching `/home/<user>/.hermes/kanban/evidence/...`, `/home/<user>/.hermes/kanban/workspaces/...` or corresponding `~` path tokens are candidates. Matching files become `[carried input]` in the placement classifier's text, preserving trailing prose punctuation. Other state, SSH instructions and unverified paths remain visible to the classifier. The card text and actual file are not rewritten.

A receipt publisher is responsible for remote installation and readback before publication; this helper verifies the receipt and local bytes, not remote machines on each placement call. Patch 2208 makes the refusal fixtures table-driven without widening the exemption.

## Code

- `hermes_cli/kanban_carried_evidence.py`: bounded receipt parsing, destination coverage, exact path and digest checks.
- `hermes_cli/kanban_placement.py::portable_reason`: filter carried inputs before home-gateway marker matching.

## Tests

Supplied source coverage, **not executed by this documentation/mirror task**:

- `tests/hermes_cli/test_kanban_carried_evidence.py::test_portable_verified_brief`: placement classifier accepts the verified input.
- `test_fail_closed`: missing/stale/future/NaN receipts, uncovered destination, bad files shape, changed bytes, absent card pin, descendant suffix, symlink and oversized input remain host-bound.
- `test_other_host_markers_preserved` and `test_no_destinations_no_exemption`: unrelated host markers and empty destination config are not exempted.
- No dedicated BOM regression is added in 2207 or 2208; BOM acceptance is visible in the decoding change but not separately tested by these patches. Remote distribution is not exercised by this suite.

## Applying

Use only the full ordered replay recipe in [README.md](README.md), including bridges and verification. Do not apply this subset alone. A carriage publisher must provision and verify inputs separately; the core change does not copy them.

## Gotchas

Adding a destination invalidates an otherwise fresh receipt unless it is covered. A hash elsewhere in the card must match the exact receipt entry; naming a directory or a descendant is insufficient. These checks are a narrow placement exemption, not a cryptographic signature or proof of continued remote availability.

## plugin?

A publisher can be external, but the exemption is core placement policy. No plugin is installed by these patches.

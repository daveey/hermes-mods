# Classic-view title test synchronization

## Behavior

The live CLI test waits for the requested title, rather than asserting immediately after the preceding session-ID line arrives. Runtime behavior and timeout budgets are unchanged.

## How it works

The fixture's `session_id(title=...)` selects the requested title as the output marker. A parametrized regression exercises normal output and three fragmented pane observations that stop after the session ID. It still checks the stored title, rendered title, token usage and active user messages.

## Code

`tests/hermes_cli/test_gateway_chat_commands_live.py`; patch **1477**, source `fb8ae4e4361c1221695d7047360c663806719f20` (PR #489).

## Tests

Executed on the frozen mirror source `a229655398887f15a65ab2a838f243611462a65d` with `scripts/run_tests.sh tests/hermes_cli/test_gateway_chat_commands_live.py::test_classic_view_title_and_usage_match_main`: **2 passed, 0 failed**, 40.0 seconds. The runner selects both normal and fragmented-title variants. This is a Linux live CLI test, not Desktop UI verification.

## Applying

Use the full bridge-aware snapshot 1478 recipe in [README.md](README.md). Full replay and tree equivalence: [mirror-1478-verification.md](mirror-1478-verification.md).

## Gotchas

A session ID appearing in a captured pane does not imply the separate title read/print has completed. Wait on the output actually asserted; do not increase sleeps or remove assertions.

## Plugin?

No. Test fixture only.

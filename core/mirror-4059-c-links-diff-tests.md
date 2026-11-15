# Links-diff raw reads and unreadable-board settling

Mirrored entries: 3864, 3889.

## Behavior

Test-only changes keep the profile-board links-diff assertion a single raw CLI read and pin the settle rule: a ledger-only entry on a board no read could access is in-flight, not a confirmed difference.

## How it works

The profile-board fixture now passes settle=0 explicitly when checking an intentionally unclean report’s exit status. Omitting the field invokes the CLI’s normal real-clock settle window, which is unrelated to that test’s one-read obligation.

A separate fake-clock settle case marks one board permanently unreadable, then checks that its entry appears under in_flight.only_in_ledger while the confirmed only_in_ledger map stays empty and clean remains false.

## Code

These entries modify only the test files listed below; production behavior is unchanged.

## Tests

Tests were not rerun by this documentation pass. The following describes assertions in the staged diffs, not fresh execution results. Full-series replay verification belongs to the parent mirror pass.

- `tests/hermes_cli/test_kanban_chat_links_profile_board.py`: Preserves exit 1 and trusted profile-board output checks while bypassing the default settle delay with settle=0.
- `tests/hermes_cli/test_kanban_chat_links_settle.py`: Asserts unreadable board B is reported, clean is false, and its unmatched ledger entry is in-flight rather than a confirmed ledger-only difference.

## Applying

Use the complete ordered series in [README.md](README.md), including its historical integration bridges, ancestry handling and mandatory post-replay hostname escape repair and repaired-tree check. These entries are not independent cherry-picks. Follow the README snapshot-specific policy/replay recipe rather than applying just this document’s subset. This document makes no deployment or live-runtime claim.

## Gotchas

No product default is shortened by this batch. Confirming a discrepancy requires at least one successful board read; unreadability is not evidence of absence. The fake-clock settle test does not measure remote availability.

## Plugin?

No new plugin or runtime behavior; these are core audit-report regression tests.

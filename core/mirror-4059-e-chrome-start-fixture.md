# Race-free Chrome startup in real-profile login tests

Mirror ordinals: 3980.

## Behavior

Lets the browser login test fixture tolerate cold starts and avoids the gap between preselecting a free port and Chrome binding it. This changes only the test launcher.

## How it works

Chrome starts with remote-debugging-port=0. The launcher removes stale DevToolsActivePort, waits for its port and browser path, and constructs the loopback WebSocket URL. Missing, partial and malformed file reads retry for up to 120 seconds; early Chrome exit still fails immediately.

## Code

Test-only patch set; production implementation is unchanged. The regression files below are the changed code.

## Tests

- `tests/tools/test_browser_real_profile_logins_live.py`: The existing real-profile login cases use the updated launcher. The diff adds no separate automated startup-delay assertion; delayed-start and throttling results in the commit message were not reproduced here.

Tests were not rerun by this documentation pass. The assertions above describe the staged diffs, not fresh execution results. Series replay and tree verification belong to the parent mirror verification; this document makes no deployment or live-state claim.

## Applying

Follow the full [README series and Applying recipe](README.md), including its historical integration bridges, ancestry-only handling where specified, snapshot-bound policy/replay plan and mandatory post-replay [hostname escape repair](hostname-escape-repair.md). These ordinals are part of that ordered series, not independent cherry-picks. Run the complete replay and repaired-tree verification rather than applying this feature subset alone.

## Gotchas

Requires an installed usable Chrome and its live-test prerequisites. A longer deadline is not a retry of failed login assertions or proof that production browser startup changed.

## Plugin?

No. These changes belong to core-owned state, lifecycle, routing or test contracts; an optional plugin cannot substitute for the ordered core implementation and its regression coverage.

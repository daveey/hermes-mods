# browser-focus

`browser_focus` raises the agent-driven Chrome only when it can verify the local browser process, or activates a matching tab through the explicitly configured remote CDP endpoint. Quiet mode still refuses focus.

## Local process custody

`ps` output supplies candidate PIDs only. The plugin reads the process executable, real/effective/saved UIDs, argv and creation time through core's pinned `psutil` dependency. The executable must be the configured `HERMES_CHROME_AGENT_CHROME` or a supported installed Chrome/Chromium binary; exactly one absolute profile must be a real descendant of the Hermes home. Renderer/headless processes, sibling-prefix profiles, ambiguous arguments and unreadable observations are not focus authority.

The selected image is checked again before reading tabs, showing Chrome, activating a target and raising its application. Creation times use core's `gateway.status.start_time_fingerprints_match` tolerance, not exact wall-clock equality. Missing core comparison support fails closed. This verifies process identity, not arbitrary transitive browser/core code or port-listener ownership.

## Explicit CDP routing

`BROWSER_CDP_URL` (then `browser.cdp_url`) selects the route instead of falling back to a locally discovered browser. An unreachable or invalid explicit route does not authorize local focus. HTTPS remains HTTPS; WSS browser WebSocket URLs derive an HTTPS discovery route. Prefix paths and query scope survive discovery and target activation. Configured fragments and embedded user information are refused. Remote focus performs CDP activation only; it does not show a local application. Explicit values that normalize to an empty URL (`/`, `///`, `#`, `?`) remain invalid configuration, never permission to discover a local browser. Loopback visibility is determined from the parsed hostname independently of path/query scope; activating a scoped loopback tab does not produce a user-visible-window claim.

## Tests

`python3 -m unittest discover -s plugins/browser-focus -p 'tests*.py'` exercises ordinary focus, selector custody, process replacement/UNKNOWN controls, transport scope and route priority with recording doubles. The no-core tests explicitly double the canonical creation-time comparator; they do not certify that core's transitive implementation has been reviewed. Run actual-core adapter checks separately when validating an installation. Unit results are not a native Chrome or installed-Mac acceptance proof.

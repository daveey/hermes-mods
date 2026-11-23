# Snapshot 5949 verification

Frozen source: `4252743d44bef7fb852ca145bb861ac8e4a47225`. This append publishes **5924–5949**
(26 non-merge commits) and 15 feature documents. The range contains no merges.
All 5,973 previously published patch and bridge files remain byte-identical.
No verifier, coverage checker, published mail or bridge was rewritten.

## Full inventory and replay

The unchanged `core/verify_mirror.py` CLI exited 0 with frozen
`mirror-policy-5949.json` and `mirror-replay-5949.json`:

```json
{"ancestry_only": 3, "base": "ea405e49a5e281558b3eeb3a13af66c6116cd0f2", "bridges": 51, "merge_base": "b46e21495f6a1d5a2547e9f196b3e6fec041ea46", "patches": 5949, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "4252743d44bef7fb852ca145bb861ac8e4a47225"}
```

The published 5923 baseline also completed full replay and tree comparison
before the append. The final run proves contiguous ordinals, unique SHA
headers, exactly one unescaped mail separator per patch, exact non-merge
source coverage, all 51 bridges and three ancestry-only entries, and the
complete normalized final source tree. Policies were frozen before that run.

## Actual Applying recipe and mandatory repair

The README's current segmented Bash recipe was separately executed with Git
2.51, including all bridges, ancestry markers, the final `[4497:1452]` slice,
and the snapshot-bound hostname escape repair. Only its initial full verifier
invocation was omitted from this second run because the identical CLI had
already completed above. The recipe exited 0 and printed:

```json
{"marker_compile": "pass", "overlay_blob": "6324662aaa1cf31fb3863d5a3b4b4ccbda095df7", "source_tip": "4252743d44bef7fb852ca145bb861ac8e4a47225", "whole_tree": "pass"}
```

Eight snapshot repair unit tests and 28 unchanged verifier unit tests passed.
The repair policy differs from the historical policy only in its exact
placement overlay postimage. Replay worktrees were removed after verification.

Preflight checked 29,905 distinct nonzero patch index IDs after replay; seven
historical abbreviated/preimage IDs remain unavailable. Both full replays and
the actual recipe succeeded without requiring those unavailable objects. This
is not a claim that every historical preimage exists in the source object store.

## Privacy, fidelity and independent review

All 26 full normalized source diffs were reproduced byte-for-byte, with only
the inherited exact whole-line mappings below applied. Source SHA headers and
ordinal order are preserved. The five changed fingerprints were independently
proven as normalized new source plus the previously reviewed line mappings:

- `hermes_cli/kanban_external_activity.py`;
- `tests/hermes_cli/test_kanban_lanes_flow.py`;
- `tests/hermes_state/test_session_status_refresh.py`;
- `tui_gateway/notice_fanout.py`;
- `website/docs/user-guide/configuration.md`.

No new normalization exceptions or replay bridges were introduced. The replay
plan differs only in its independent source-tip binding. A separate-session
review reproduced the source-diff and fingerprint proofs, checked all new mail
and feature-document privacy, confirmed immutable published bytes and verified
README slice coverage. Its remaining acceptance-receipt link finding is
resolved by this document. Generic source comments and artifact basenames do
not identify a private host or account and remain unchanged.

Incident-derived mail prose (task/profile IDs, live timestamps, run/PID values,
private debug narratives and measurements) was replaced with technical change
summaries. Diff bodies retain source semantics and existing reviewed public
normalizations; the hash-pinned, already-public companion fixture is intact.
Full private-denylist content/filename checks, staged privacy hooks and feature
coverage passed. Whitespace checks cover docs, JSON and Python, not mail
context/trailers. No new gitleaks exception was required.

## Frozen artifact SHA-256

| File | SHA-256 |
|---|---|
| `mirror-policy-5949.json` | `23c3beba65b8ceba952ea9ddaaf5250e159200a291919a69c4a4de853c1ab4ce` |
| `mirror-policy-5949-escape-repair.json` | `5a595a2b74303f9bfeb901c8899633deb6cffb3fbdb0f8c49f609a015685ec39` |
| `mirror-replay-5949.json` | `7500033f232a216e2d22602df1372941a0977f6ade23a903d6e1333a573831b3` |

## Scope limits

This verifies the public mirror, not fresh execution of every implementation
suite. Feature documents explicitly distinguish inspected source regression
coverage from tests actually run here and from live deployment. The shared
`needs_you` change is patch 5947; its companion session-panel behavior and final
Mac red-state parity were not exercised by this mirror. The implementation
handoff reports a backend-startup prerequisite for that live check. No new lane
implementation beyond the mirrored source, hotkey change, native deployment,
restart or performance acceptance is claimed.

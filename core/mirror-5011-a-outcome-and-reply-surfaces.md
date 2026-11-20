# Outcome derivation, reused streamed replies and opaque notice sidecars

Mirror snapshot 5011; ordinals 4393, 4414, 4422.

Entries:

- **4393**: fix(outcomes): reject empty card lists contradicted by sidebar counts
- **4414**: Preserve opaque notice model sidecars during constituent repair
- **4422**: fix(gateway): settle reused streamed replies in local viewers

## Behavior

- **Outcomes (4393)**: an empty (possibly stale-cache) remote card list used to count as proof of no work, so a chat could show done while the sidebar still counted an open card. Active/blocked counts are now confirmed across the compression lineage only when the list is empty; a disagreement or unreadable counts yields `card_read_failed`, never done, with normal decision/wait precedence preserved.
- **Local viewers (4422)**: captured reused finals are restored into local completion frames when adapter delivery returns None, and interim seals are published with the actual streaming flag. Ordinary streaming and external delivery are unchanged.
- **Opaque notice sidecars (4414)**: constituent repair clears invalid span identity (identity controls deduplication) but no longer erases a string `api_content` payload; model/display separation and non-string fallback are retained.

## How it works

`hermes_state_outcome.py` does the count confirmation; `gateway/run_turn_runner.py` and `session_settlement_recovery.py` carry the viewer frame; `agent_runtime_helpers_user_rows.py` carries the repair rule.

## Code

Shipped non-test paths (union over these entries):

- `agent/agent_runtime_helpers_user_rows.py`
- `gateway/run_turn_runner.py`
- `gateway/session_settlement_recovery.py`
- `hermes_state_outcome.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/agent/test_opaque_notice_sidecar.py`
- `tests/gateway/test_local_viewer_events_turn.py`
- `tests/gateway/test_local_viewer_transformed_final.py`
- `tests/hermes_state/test_session_outcome.py`
- `tests/tui_gateway/test_remote_card_outcomes.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- The historical stale-cache cause was reproduced as a possible mechanism, not proven for any specific past event.
- Electron acceptance of 4422 was not part of these entries.

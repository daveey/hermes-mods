# Done replica cards remain pending until verified release

Patches: 3837, 3838.

## Behavior

An unreleased replica link whose card is known done now displays `done_pending_release` instead of `unknown`, without dropping the originating chat's unfinished obligation. Missing, statusless or archived-without-release cases remain unknown.

## How it works

`kanban_session_activity_release` centralizes `UNKNOWN`, `DONE_PENDING_RELEASE`, `PENDING_RELEASE_STATUSES` and `unreleased_obligation`. Replica-card projection selects the new label only for a present done row that is not released. Listing, counts, activity buckets, parked/stalled handling and notice fanout accept both pending labels, so the clearer presentation does not accidentally release or stop observing the card.

Once the owner verifies completion and releases the link, the normal done/released row can stop counting. The follow-up test updates a closed-card/profile filter fixture to expect the new label while preserving `released=False`. The origin-ledger reader's historical unknown label is unchanged.

## Code

- `hermes_cli/kanban_chat_links.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_activity_release.py`
- `tui_gateway/notice_fanout.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/hermes_cli/test_kanban_activity_revision.py` (patch 3837).
- `tests/hermes_cli/test_kanban_chat_links.py` (patch 3837).
- `tests/hermes_cli/test_kanban_chat_links_parity.py` (patch 3837).
- `tests/hermes_cli/test_kanban_chat_links_pending_release.py` (patch 3837).
- `tests/tui_gateway/test_remote_card_outcomes.py` (patch 3837).
- `tests/hermes_cli/test_kanban_chat_links_released.py` (patch 3838).

- **3837:** Replica link/parity tests keep done_pending_release counted and unfinished until release, including never-verifying cards; missing/statusless/archived rows remain unknown. Activity revision and remote-outcome tests keep observing the release transition.
- **3838:** The closed-card/profile reader expects done_pending_release with released=False for the unreleased done row while retaining profile filtering and released/archived controls.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

Done is not synonymous with verified release. A card that never verifies remains an unfinished obligation indefinitely. This is specifically the replica-reader presentation; do not claim the origin-ledger path was relabelled. Remote outcomes still need to observe the later release transition.

## Plugin?

No. The label participates in core link projection, activity counts and outcome observation, not just a replaceable UI chip.

# Private-hold diagnostic: released capability stops and stacked historical releases

**Scope:** Private-history classifier recognises exact capability-stop releases; explicit stacked capability release option.

**Source ordinals:** 6388, 6389, 6390, 6553

## Behavior

- A capability-unavailable stop counts as released only when a later version-1 release names that exact stop uid, the release was authenticated by the caller (on a synced board/Board observer its Board-stamped origin gateway equals the card's home gateway; payload actor/authority text never authenticates), and a promotion/archive is recorded after it; otherwise it stays restriction-unattributable.
- A card's home gateway can read its own release before the Board echo arrives (envelope-less release accepted only where the board identity equals the card's created_on); replicas' envelope-less rows and replaced envelopes stay held.
- The guarded single-card home release gains a repeatable capability-episode option to release exact historical capability stops, resolving only same-card evidence and revalidating independent scope/safety/containment checks.

## How it works

- Logic is in `kanban_actionability_private.py`, `_sources.py`, `_observation.py` and `kanban_release_capability.py`; the CLI flag is declared in `kanban_parser.py`.

## Code

Files touched by the series (paths relative to the source tree):

- `hermes_cli/kanban_actionability_observation.py`
- `hermes_cli/kanban_actionability_private.py`
- `hermes_cli/kanban_actionability_sources.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_release.py`
- `hermes_cli/kanban_release_capability.py`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/test_kanban_actionability_private.py`
- `tests/hermes_cli/test_kanban_release.py`
- `tests/hermes_cli/test_kanban_release_stacked_capability.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6388-6553, 4 commits: 6388, 6389, 6390, 6553) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- A newer scope release never authorises an older capability episode.
- Mutation tests cover ordering, version, unsynced-board and created_on guards.
- Safety evidence, triage cards, unknown/decision-pending causes, scope stops, stale, wrong-episode or unverified releases stay fail-closed; the Board observer supplies no mandate digest, so scope-renewed history stays mandate-unverifiable there.
- The stacked capability release is a pending-current-decision operation (not a standalone rewrite of an already-running card); a no-op/resume cannot accept a new selection unless matching receipts already exist from that recorded release.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

The classifier and release command are part of the core authority/audit chain.

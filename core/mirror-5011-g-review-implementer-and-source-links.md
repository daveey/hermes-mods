# Review evidence: routed implementer declarations and replica source links

Mirror entries 4958 and 5011.

## Behavior

- **4958:** a card's declared implementer models must each be a route model of the assignee's *enabled routing policy*.
  Previously a host without a managed registry (no profile config, routing off, or `enforce_subscription_only` false, the default)
  accepted any well-formed name, which lifted the fail-closed "implementer evidence is unavailable" refusal and let the reviewer be the real implementer.
  Now no policy, an unknown profile or an unreadable config voids the declaration. A CRLF body no longer drops the marker.
- **5011:** a review card picked up on a worker keeps its links to the source cards. Pickup only copies parent edges and the feed
  keeps a link only when its child is local, so such links were lost, leaving no implementer evidence. Review evidence now lists a card's linked children from the live file and, on a synced board, from the replica.

## How it works

- `kanban_review_model.py`: `declared_implementers` requires positive membership in `_routable_models` of the assignee's enabled policy;
  the managed-registry check stays on top; the unreadable-config path voids instead of raising from `implementer_models`.
- `kanban_review_sources.py` reads children via `source_reader.children` from the board replica; a local source wins over the replica copy,
  and a linked review with no replica stays fail-closed. Links added after pickup are also covered.

## Code

`hermes_cli/kanban_review_model.py`, `hermes_cli/kanban_review_sources.py`.

## Tests

Source tests (not executed here): `test_kanban_review_declared_implementers` (routing-config-backed fixtures: host without a policy,
name outside the policy, unknown profile), `test_kanban_review_source_replica` (`test_local_source_wins_over_the_replica_copy`,
`test_picked_up_review_reads_its_source_links_from_the_replica`, `test_linked_review_without_a_replica_stays_fail_closed`).

## Applying

Never apply independently; use the full segmented recipe in the core mods [README](README.md) ("Applying").

## Gotchas

- Tests of declared implementers must now run under a real routing config, or they will see voided declarations.
- Replica reads only apply on synced boards; unsynced boards rely on the live file.

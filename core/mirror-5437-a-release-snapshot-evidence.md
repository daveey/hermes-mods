# Exact terminal releases and reused owner evidence for release snapshot tokens

Mirror entries: 5417, 5418.

## Behavior

- A previously observed, verified terminal release does not return to a chat's Waiting list merely because replica transport freshness expires.
- Preservation requires the same validated raw owner-observation hash and the complete owner/chat identity. A different identity or owner record cannot borrow an earlier release.
- Reopened, changed, tampered, unverified and evidence-free records still wait. A cold stale completion cannot acquire its first release through this mechanism.
- Durable origin-work snapshots retain the release token across restart; old snapshots without a token are not completion proof.
- Both replica projectors reuse owner evidence already decoded and verified, instead of hashing the raw record again solely to create a release snapshot token.

## How it works

`historical_fields` combines completion-receipt fields with `observation`. The latter issues `_release_observation` only for raw `done` or `archived` records whose gateway, profile, board and task identifier exactly match the qualified key and whose evidence is explicitly verified. An absent owner profile is rejected. The token is the validated evidence's `task_hash`, captured before stale projection can turn a terminal status into `unknown`.

The legacy remote-links producer calls `preserve(cards, previous)` before reconciling missing rows. Previous candidates must already be released, terminal and token-bearing. Matching includes `key`, `session`, `gateway`, `profile`, `board`, `id`, `origin_gateway`, `origin_profile` and `via`.

Only a stale current card with that exact identity and the same token receives the prior terminal status, `released=True` and `unfinished=False`. Fresh rows retain their normal projection result. This preserves an established release rather than treating stale transport as new negative completion evidence.

Entry 5418 removes the snapshot helper's independent `_owner_evidence` call. The staged projector retains decoder evidence on `_Stage`; the explicit-clock projector retains it in an observations map keyed by owner identity. Both pass that evidence through `historical_fields` to `observation`. Token eligibility and release identity gates remain unchanged.

## Code

Paths refer to the mirrored core source tree.

- `hermes_cli/kanban_terminal_release_snapshot.py`
- `hermes_cli/kanban_replica_projection.py`
- `hermes_cli/kanban_session_activity.py`

## Tests

**Coverage inventory only — source tests were not executed by this mirror documentation task.**

The added regression exercises `reconcile_links` through the legacy remote-links producer and durable snapshot, rather than testing only isolated fresh and stale projections. It starts with an unreleased running card, acknowledges a terminal notice through the real delivery ledger, verifies a one-pass done release, then advances transport age beyond the stale threshold and checks that release remains set.

A restart case clears the in-memory remote snapshot before the stale pass. Negative cases replace the raw owner record with reopened, newly completed, tampered, unverified or missing-evidence data and require the link to wait. A cold stale completion also remains unreleased.

Helper controls change each identity field individually and reject borrowing the prior release. Additional controls reject snapshots that are unreleased, nonterminal or tokenless. These added tests use `done`; preservation of `archived` releases is untested by the added cases.

Entry 5418 changes no test files. The added transport-age suite is correctness coverage for release preservation, not a direct proof of decoder-cache reuse. Both the staged-projector reuse path and the explicit-clock reuse optimization are untested by that added suite as performance properties; no performance result is claimed here.

Source test path added by these entries:

- `tests/hermes_cli/test_kanban_terminal_release_transport_age.py`

## Applying

Apply these entries only as part of the **whole ordered 5437 patch series**, including the snapshot replay bridges and the mandatory post-replay hostname-escape repair. Follow [the core README applying recipe](README.md), including its inventory/tree verification and repaired-tree check. These entries are not advertised as independently applicable patches. This documentation task did not replay patches in isolation.

## Gotchas

Transport staleness alone neither proves completion nor invalidates an exact completion already released. First release still uses the existing fresh-evidence gate. Snapshot persistence does not authorize a changed owner record, and old tokenless snapshots cannot be upgraded by assumption. No acknowledgment policy, SQLite schema, task lifecycle gate or replica freshness threshold is relaxed. Callers of `observation` must supply evidence from the corresponding decoded record; the helper no longer independently recomputes it.

## Why core instead of a plugin

Owner-evidence validation, replica projection, durable origin-work snapshots and chat-link reconciliation are core data paths. Exact release preservation must use their shared identity and evidence contracts before Waiting state is published; a presentation plugin cannot safely reconstruct or override those contracts.

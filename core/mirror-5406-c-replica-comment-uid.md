# Portable comment identity for replica PR provenance

Mirror entries: 5304, 5305. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Replica comment events can map to their local comment row by a unique portable `comment_uid` even when local integer IDs or legacy stored-length metadata differ. Ambiguous UIDs do not authorize a match. Newly recorded comment length reflects the stripped stored body; finished-job wake notes have no surrounding whitespace.

## How it works

The comment-row index carries UID-to-local-ID candidates alongside author/time/length metadata. A unique portable UID wins; missing/duplicate UIDs fall back to the existing conservative matching proof. Legacy unstripped lengths remain supported only with the event's own integer/author/write-clock evidence. External-watch note construction joins sentences without leaving a trailing space on successful outcomes.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/kanban_db_comment_ids.py`
- `hermes_cli/kanban_db_dispatch_pr_state.py`
- `hermes_cli/kanban_external_watch.py`
- `hermes_cli/kanban_release_comments.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source tests distinguish a foreign replica wake note mapped by UID from the same note without UID, retain own-run PR holds, exercise duplicate-UID fallback with discriminating ambiguity, check stored stripped lengths and both success/failure wake-note whitespace. No live replicated GitHub comment round is executed here.

Relevant source suites/fixtures:

- `tests/hermes_cli/test_kanban_pr_replica_comment_uid.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

A duplicate portable UID is not proof; the fallback must remain fail closed. Length alone cannot disambiguate historical comments. This maps provenance and does not itself release a review or bless a PR.

## Why core instead of a plugin

Core comment writes and dispatcher provenance reads must share one identity contract across replicas. A plugin cannot repair integer-ID assumptions inside the decision transaction.

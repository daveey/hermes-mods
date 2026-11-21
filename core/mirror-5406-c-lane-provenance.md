# Lane matching from explicit bounded provenance

Mirror entries: 5308. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Lane rules can match exact skill names, title patterns and bounded repository provenance without treating an arbitrary ancestor directory as the card's repo. `hermes kanban lanes reclassify` previews rule matches for unclassified/inherited default-lane open cards; `--apply` repairs labels only, preserving execution custody/admissions/holds/priority.

## How it works

Repository identity uses the basename of `project_repo`/`workspace_path` (with `.git` suffix removed) and the component immediately before `.worktrees`, with no filesystem/Git/network lookup inside creation. Skills are parsed from a JSON/list/tuple value and matched as exact strings. Policy validation accepts the new skill/title fields and validates regex patterns.

Reclassification scans nonterminal default-lane tasks whose lane source is unclassified or inherited, resolves stored project provenance, runs normal rules and optionally updates tenant plus an assignment event with source `rule`. Explicit user/planner labels and previous rule decisions are not overwritten. A gated destination label does not release its execution gate.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `hermes_cli/kanban_lane_matching.py`
- `hermes_cli/kanban_lane_reclassify.py`
- `hermes_cli/kanban_lanes.py`
- `hermes_cli/kanban_lanes_cli.py`
- `hermes_cli/kanban_parser.py`
- `website/docs/user-guide/features/kanban-worker-lanes.md`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Source tests cover repo/worktree identity, exact skills, title rules, invalid patterns and ancestor false positives. Database fixtures check reclassification across execution states/block kinds, preserved custody/manual labels, and normal creation-gate/explicit-lane behavior. The new CLI parser/command wrapper is present, but this batch adds no dedicated argparse-to-command execution test: that entry path is untested.

Relevant source suites/fixtures:

- `tests/hermes_cli/test_kanban_lane_matching.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

Dry run is the default. Relabeling a gated lane is accounting repair, not admission or authorization to execute. Title regex matching is explicit policy, not permission to scan all task prose or every filesystem ancestor.

## Why core instead of a plugin

Lane assignment runs during core card creation and reclassification transactions and must preserve admission/lifecycle state; a plugin relabeler cannot reliably replace those transactional semantics.

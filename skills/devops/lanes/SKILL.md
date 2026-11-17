---
name: lanes
description: "Use when the user asks what the fleet is working on, wants more or less of a project (a lane), or when running the lane planner."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [kanban, lanes, scheduling, priority, planner]
---

# Lanes: steering what the fleet works on

A lane is a project. Each Kanban card belongs to one lane, and each lane has a share: its relative volume of
worker time. Shares are targets, not ceilings: a lane with nothing ready lends its slots to the others. A gated
lane holds the cards agents file for themselves until they are admitted.

Everything goes through `hermes kanban lanes`. Never edit card priorities or `config.yaml` by hand for this.

## Read the state first

```sh
hermes kanban lanes show --json          # target vs actual share, running, ready, held, open, done, cost per lane
hermes kanban lanes show --hours 24      # a different window (1, 6, 24 or 168)
hermes kanban lanes held --json          # cards waiting at a gate
hermes kanban lanes unclassified --json  # open cards nothing placed in a lane
hermes kanban lanes explain <card>       # one card: its lane, where that came from, why it waits
```

A lane under its target with `ready: 0` is short of cards, not slots. Say that; changing shares will not help it.

## When the user asks for a change

Turn the sentence into the smallest set of `set` commands, show them, run them, then show the new `show`.

| They say | Run |
|---|---|
| "more X", "X first this week" | `hermes kanban lanes set X --share <higher>` |
| "stop X", "pause X" | `hermes kanban lanes set X --pause` |
| "resume X" | `hermes kanban lanes set X --resume` |
| "X only for breakage" | lower its share; if it is gated, also `--admit-per-day <lower>` |
| "run this card now" | `hermes kanban lanes admit <card> --reason "<their words>"` |
| "leave this card's priority alone" | `hermes kanban lanes pin <card>` |
| "this card belongs to X" | `hermes kanban lanes assign <card> X --reason "<why>"` |

Shares are relative, so raising one lane lowers every other lane's target. State the resulting targets from
`show`, not your own arithmetic. To undo, set the previous values back.

## Running as the planner

The planner proposes; `apply` decides. Write one JSON list and pass it once:

```sh
hermes kanban lanes apply plan.json --json
```

```json
[
  {"action": "relane", "card": "t_…", "lane": "product", "reason": "firmware change for the product"},
  {"action": "admit", "card": "t_…", "reason": "the hub cannot start workers until this lands"},
  {"action": "merge", "card": "t_…", "into": "t_…", "reason": "same failing check, already owned"},
  {"action": "reprioritize", "card": "t_…", "priority": 700, "reason": "blocks three ready cards"},
  {"action": "note", "lane": "product", "text": "Under target: nothing ready. Needs new cards, not slots."}
]
```

Rules `apply` enforces (a refused action comes back with its reason; do not retry it reworded):

- `admit`: at most the lane's daily budget, never a title the lane lists under `never_admit`.
- `merge`: a held card into an open card of the same lane. The merged card stays held. Nothing is archived.
- `reprioritize`: 0 to 1000, never a pinned card.
- `relane`: only cards placed by inheritance or by default, never into or out of a gated lane.
- Every action except `note` needs a one-line reason. At most 50 actions per plan.

How to choose:

1. Label every unclassified card first (`relane`), from its title and body against the lane descriptions.
2. For each gated lane, read the held cards. Merge duplicates into the card that already owns the problem.
   Admit, oldest first, only cards where something is broken now or another lane's work waits on it. Leave
   improvements held; the budget is a ceiling, not a quota to spend.
3. Inside each lane, raise the cards that unblock the most other cards or that the user asked about; lower
   nothing without a reason the user would accept.
4. Write one `note` per lane that is off its target, saying why in plain words.

The planner never changes shares, never creates cards and never archives. Those are the user's.

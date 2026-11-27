---
name: lanes
description: "Use when asked what the fleet works on or to shift shares."
triggers:
  asks:
    - "what is the fleet working on?"
    - "give this project more worker time"
    - "run the lane planner"
  not_for: "auditing blocked cards (use kanban-hands-digest)"
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

On a pool worker, the local CLI write fence may refuse even `lanes assign`. Use the supported `hub-admin 'hermes kanban lanes assign <card> <lane> --reason "..."'` route when hub-write access is installed, then read back with `hub-admin 'hermes kanban lanes explain <card>'`. Do not unset worker identity or mutate the board with raw SQL to bypass the fence.

## Read the state first

```sh
hermes kanban lanes show --json          # target vs actual share, running, ready, held, open, done, cost per lane
hermes kanban lanes show --hours 24      # a different trailing window, in hours
hermes kanban lanes held --json          # cards waiting at a gate
hermes kanban lanes unclassified --json  # open cards nothing placed in a lane
hermes kanban lanes explain <card>       # one card: its lane, where that came from, gate state (~10 s)
```

`lanes explain` does not say why a ready card isn't starting: lanes only reorder/pause/gate, and every other governor (pacing, labels, host slots, walls, spend…) runs after lane order. For "why is this ready card idle" read the newest `dispatch_skipped` event and `worker_queue_reason` (`kanban_dispatch_skip.latest` / `kanban_queue_reason.latest`, milliseconds) or the `queue_note` in a `cross_task` read.

Scope: `scope: fleet` means one board's replica across gateways, not every board. Other hub boards are separate DBs, and the mini's Baby Bear board is a separate Hermes install (read it via `tailnet_exec` host `mini`) that no hub lane counts, so a hub `babybear` pill omits its cards.

A lane over its target: sum `task_runs` hours by `tasks.tenant` over 48 h, group that lane's cards by `created_by`
and title topic, and `explain` a few. Source `unclassified` means no match rule caught it and it fell to the default
lane (typically chat-filed cards); fix classification, not shares, and `reclassify` returning no changes confirms it.

A lane under its target with `ready: 0` is short of cards, not slots. Say that; changing shares will not help it.

For lane-accounting repairs, distinguish scopes: `show` uses the current board's fleet replica when available, while `unclassified` and `reclassify` read the current owning board. Audit the fleet's open `other` rows, then run supported dry-run/apply on each owning board; never write the fleet replica. Read back each changed card's actual tenant after synchronization: an empty repeat dry-run or a `lane_assigned` event alone does not establish that the tenant persisted. Preserve containment and custody if a protected row reverts. A successful `lanes assign` message can accompany a `lane_assigned` event while a protected contained triage row retains its old tenant: verify the persisted tenant, record the unsuccessful move separately, and never release containment or repeatedly reassign it to make a migration count look complete. Resume a timed-out multi-card apply from per-card readbacks, skipping terminal/already-moved cards; persist receipts on disk before aggregating. The run-hour report joins runs to each card's current tenant; open-card repair intentionally leaves terminal historical cards unchanged, so quantify their residual run-hours rather than promising an immediate historical drain.

For a semantic-classifier live proof after deployment, use a fresh hidden scratch chat with no explicit lane, parents, skills or deterministic rule metadata; an older worker process still imports its pre-deploy classifier. Read back `lanes explain` for `source=classified`, archive the exact unclaimed fixture immediately, delete the scratch chat, and independently verify both cleanups. A direct helper replay proves model routing, not the chat-create transaction path.

Before changing shared lane matching policy, inspect the host's `shared_config_exclude`: exclusions match dotted prefixes as well as exact keys, so excluding `kanban.admission.lanes` also excludes a separate `kanban.admission.lanes.hermes.match` shared key. Preserve the excluded parent map and use the supported host `shared_config_host_values` match-only override (which wins over exclusions), keeping its value equal to the shared source match policy. Install matcher support first; test real `apply_shared_config` exclusion/override behavior and verify installed matches plus unchanged live shares, gates and budgets.

When extending the lanes dashboard, match the CLI's fleet connection selection for both summary and flow reads; close an owned replica even when snapshot generation fails. A flow card's `pr` is its own PR, not its dependency's PR: without explicit blocker evidence, say it waits on an open parent rather than claiming which merge frees it. The Desktop lane strip (user's spec): lane pills carry no lane colour, only the name plus blocked-on-you (red), running (blue) and other (grey) counts; zero lanes hidden; the right side shows all-lanes totals in the standard card colours (red on you, pink CI/review/deploy waits and landing steps, amber parked clock/chat/external waits, purple held at a gate, green waiting for a worker, blue running, grey waiting on other cards, outlined triage). Lane-gate and `limit:*` cards are purple, never green: a free worker would not start them. Anything showing held cards, the strip or a chat reply, names the lane and the gate (`hermes lane gate`, `bb $/day limit`) plus admitted-today/budget, never a bare "held" or "gated" (user's spec). Held cards filed by automations (sweepers, watchers, self-heal) have no chat session, so the session pane shows no rows for them. Say so and name the filer (the view's `gates[].unlinked` gives counts per `created_by`); don't hunt for a missing link. Compute lane counts and totals on the hub from the same flow card reasons so lanes add up to the totals, and keep that sum visible: each pill's blue numeral is the lane's TOTAL running, and over-share is an extra mark beside it. If the running count is split into an in-share capsule plus a `+over` tag, the user adds up the blue numbers, they don't match the global blue, and he reads it as missing cards. A pill's red count has to match the red rows in the session pane. When the user reports a count mismatch or asks why an ask sits under "Unlinked asks", follow `references/count-reconciliation.md`; when the strip is slow or shows a timeout error, follow `references/strip-latency.md`. Test daily-cap writes against an older `set_lane` signature and reject unsupported fields before any write with a clear 409, while preserving share-only writes.

For all-board dashboards, propagate flow completeness beyond governor counts into status, attention, cost and exact-set/session navigation. Track summary acquisition separately from flow completeness: flow-only failure may retain independent summary counters, but a whole-board read failure invalidates fleet/lane summary totals and actual-share denominators too. Assert missing waiting cards render as unknown, not zero, through the real dispatcher and sidebar DOM, then restore the source and verify recovery. Test failure in both the first and a later source: merge order must not turn a partial list into exact-looking zeros or suppress healthy sibling lanes. Isolate sibling cursor-read errors too, retaining failed-path native coverage and testing public change_cursor through a healthy sibling change and failed-path recovery; projection-only failure tests miss a stream-breaking cursor exception.

When consuming core governor snapshots, keep complete governor counters separate from capped per-card receipts: `truncated` makes affected lane counts and exact held-card navigation incomplete, not a smaller authoritative count. Carry each board's flow-read completeness independently of its tick freshness; a fresh tick cannot certify a failed flow read as zero held or “starting”. Mark affected lane details unknown/disable exact-set navigation while retaining healthy sibling lanes and full known global counters. Prove this through real dispatcher fixtures with a reduced receipt cap and an injected flow-read failure, then assert the rendered sidebar; persist selected lane as well as section expansion and test unmount/remount.

## Sidebar layout and composed SDK coverage

Keep minimized LANES and GOVERNORS to one unwrapped row each: title left, compact details right-aligned in the remaining width, chevron at the far right. Put cost/share bars, readings and lists only in independently persisted expanded bodies. Truncate secondary text rather than wrapping; expose its full value accessibly. Test real browser geometry at narrow widths and loading/offline/partial states, not just DOM presence.

Mount above-Pinned sidebar and Sessions-tab totals together; verify totals placement, status selection, actual session-filter matching/clear/unmount cleanup and the not-live indicator. Reuse one state/filter owner for modern totals-only and legacy strip layouts; registering a tab contribution is insufficient if its state owner is never mounted. Keep totals-only subscribers passive: sidebar owns expanded/window parameters, including when totals mounts first, remounts, or receives Show-lanes events. Test both mount orders with real collapsed/expanded producer views and configured cap inputs; a collapsed view omits limits and can look unlimited. Ignore persisted legacy lane-pill selection when those pills are absent, without deleting the older layout's saved selection.

Use core freshness_bound(doc) for governor cursor expiry and exclude tick completion/gap diagnostics from semantic decisions while still renewing freshness deadlines. Preserve unknown newer governor ids as unknown rows with charged counters; version skew must not assert the whole lanes view offline.

## Multi-board projection correctness

Arm each SQLite deadline when that board's work starts, not when sibling connections open. Contain context, summary and receipt failures to their source; one interrupted sibling must not take the view offline. Prove slow first and later boards with real dispatcher DBs, actual expired progress handlers, healthy sibling rendering and recovery. Share unknown/partial summary formatting between the sidebar and old-SDK strip/panel; assert both mounts from one real projection, including visible unknown lanes, shares and governor held counts.

## When the user asks for a change

Use the smallest applicable command below, then read back the result: `show` for shares, `explain <card>` for assignment. When creating cards under an established project-lane direction, assign and verify the lane as the creator rather than leaving that administrative step in the worker's brief. If a worker returns a creator-only lane request, run `assign`, read back with `explain`, and post the evidence via `kanban_comment` so the parked task can finish; this avoids another worker run merely to attempt a mutation outside its authority.

| They say | Run |
|---|---|
| "more X", "X first this week" | `hermes kanban lanes set X --share <higher>` |
| "stop X", "pause X" | `hermes kanban lanes set X --pause` |
| "resume X" | `hermes kanban lanes set X --resume` |
| "X only for breakage" | lower its share; if it is gated, also `--admit-per-day <lower>` |
| "run this card now" | `hermes kanban lanes admit <card> --reason "<their words>"` |
| "leave this card's priority alone" | `hermes kanban lanes pin <card>` |
| "this card belongs to X" | `hermes kanban lanes assign <card> X --reason "<why>"` |
| "this chat's/project's work belongs to X" | assign every open card of the chat, then add a match rule (below) |

Whole chat or project: list the cards with `hermes kanban chat-tasks <session_id>` (drop done/archived), then loop `assign` + `explain` in a `background=true, notify=true` terminal job that prints `<id> <lane>` per card. Each call takes several seconds, so a foreground loop over dozens of cards times out partway; resume from the printed readbacks and only re-run the cards that weren't moved. To make the project's future cards land there too, extend the lane's match with `hermes config set kanban.admission.lanes.<X>.match.<repos|title_patterns|key_prefixes> '[...full list...]'` (re-list the existing entries; the "not a recognized config key" warning is harmless). Direct file edits to config.yaml are refused. Test the rule with `.venv/bin/python` in `~/.hermes/hermes-agent`: `kanban_lanes._rule_lane(kanban_lanes.policy(), {"title": ...})`. A rule beats an inherited lane but not one the user set explicitly, so `reclassify` showing no changes after a manual move is expected.

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

# Kanban: describe held cards from their containment episode; wake or escalate once, respecting remote ownership

Mirror entries 5168-5173.

## Behavior

- A held triage card with a containment episode was described from an older park event, so the owner wake never fired and the triage sweep skipped it. Wait info now reads the unreleased episode, the authority and producer status (board file, else replica; unknown counts as open) and when the authority finished. Open authority or producer: not agent-owned. Authority done/archived for 30+ minutes with no open producer: agent-owned, certain, stalled at once, dated from the authority's completion; the wake premise includes episode and completion so it fires once per episode.
- The stale-triage sweep gives a stranded held card with no chat link one supervisor card (key per task and episode) plus a comment on the held card; no event is written on the held card.
- 5169: a worker no longer escalates a retained foreign contained copy (escalation runs after the foreign-copy fence).
- 5170: archived authority uses the archive transition clock, not later comments; already escalated episodes skip chat link scans; containment age shows while the authority is open.
- 5171-5173: ownership uses owning links on all gateways and owning hub subscriptions (shepherd subscriptions count), read per card, preserving explicit releases, machine/notice exclusions, resolved local creator links and fail-closed read errors.

## How it works

- `hermes_cli/kanban_contained_wait.py` (episode read), `kanban_wait_info.py` (`describe_contained`), `kanban_session_wait_rows.py`, `kanban_triage_sweep.py` (escalation and ownership).

## Code

- `hermes_cli/kanban_contained_wait.py`
- `hermes_cli/kanban_wait_info.py`
- `hermes_cli/kanban_session_wait_rows.py`
- `hermes_cli/kanban_triage_sweep.py`

## Tests

Source tests (not executed in this mirror pass): `tests/hermes_cli/test_kanban_contained_strand.py` (authority done over/under 30 minutes, open producer, premise stability, one card per episode, no event on held card, dispatcher sweep runs once), `test_kanban_contained_strand_review.py`, `test_kanban_triage_replica.py` (worker sweep does not escalate a retained copy), `test_kanban_contained_remote_owner.py` (remote ownership, unreadable ownership, hub subscription before link row lands, per-card ownership read, released creator link).

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Unknown authority status counts as open, so a card is never escalated on a failed read.
- The 30-minute settle delay is a constant in the wait-info code.

## Why core instead of a plugin

Wake and escalation read core kanban episode state and chat link ownership inside the dispatcher sweep.

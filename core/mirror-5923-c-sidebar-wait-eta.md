# Light-blue timer waits and sidebar ETA readouts

Patches: **5918–5921**. Extends the existing external-wait sidebar and canonical outcome projection.

## Behavior

Declared clock waits join CI/deploy waits in the light-blue external state. A timer-only row draws a plain stripe without a service cloud. A light-blue row with ETA fields replaces its card-colour squares with waited / expected / remaining; other rows and older projections without ETA keep the squares. The gateway mark stays last.

Remaining time counts down from the client clock. Missing estimates show `?` and a dash rather than invented progress. Over-estimate remaining time becomes orange `+over`. The soonest-ending wait leads; hover/accessibility text lists all readouts and estimate bases.

Replica count snapshots now carry the same wait evidence used for their parked counts. Declared remote timers need no service watch; remote CI/deploy health stays unknown with no locally invented history estimate. Stale replica evidence has unknown counts but no ETA or healthy timer claim.

## How it works

`eta_fields` provides `wait_kind`, `waited_since`, `expected_s` and `estimate_source`. A clock expectation is wake time minus park time, bounded below by zero. CI/deploy uses a median of up to 20 recent finished waits for the same normalized repository or host/service target, requiring at least three positive samples. History scans at most 600 newest park events and memoizes by database/kind/target for 60 seconds. The first supported end event after a park ends that sample; unfinished waits do not contribute.

Local wait readers overlay history and external-card projections carry the ETA. Timer external state has no poll times. Canonical outcome/status projection maps these waits to the existing external stripe. Replica markers encode qualified-card lineage plus immutable ETA payload in the existing frozen count channel, avoiding a second replica read and inconsistent generations. Lists cap external cards at ten without capping counts.

The renderer uses a one-second tick near seconds-scale boundaries and ten-second ticks otherwise. It ignores down-service ETA candidates and leads with the smallest known remaining duration. Follow-ups align outcome consumers, replica timers and typed frontend fixtures with the new state.

## Code

- `hermes_cli/kanban_wait_info.py`, `kanban_wait_history.py`, `kanban_session_wait_rows.py`: ETA derivation/history.
- `hermes_cli/kanban_external_activity.py`, `kanban_session_activity.py`: local external cards and replica snapshot markers.
- `hermes_cli/sessions_projection_manifest.py`, `hermes_state_outcome.py`, `hermes_state_session_status.py`: schema and canonical outcome carriage.
- `apps/desktop/src/lib/wait-eta.ts`; sidebar `session-row-card-squares.tsx`, `session-row-stripe-external.ts`, `session-row-stripe.tsx`, `session-row.tsx`: formatting, live readout and timer stripe.
- Desktop `src/plugins/kanban/external-run.ts` and `src/types/hermes-session-activity.ts`: external-card types and timer hover.

## Tests

**Source regression coverage; not executed in this mirror pass.**

- `tests/hermes_cli/test_kanban_wait_eta.py`: clock arithmetic/labels, same-target median/history cap/minimum, unfinished samples, replica derivation, local external cards and canonical light-blue outcome.
- `tests/hermes_cli/test_kanban_replica_wait_eta.py`: frozen marker survival, lineage dedup, stale evidence, count/list caps and single-read remote CI/deploy/clock-to-sidebar carriage.
- `tests/hermes_cli/test_kanban_session_outcome_tasks.py`, `tests/tui_gateway/test_session_outcome_notices.py`: migrated clock outcome consumers.
- `tests/hermes_cli/test_kanban_remote_waiting_overlap.py`, `tests/hermes_state/test_session_status_refresh.py`: remote timer unknown-count and canonical stripe expectations.
- Desktop `src/lib/wait-eta.test.ts`, sidebar `session-row-card-squares-eta.test.tsx`, `session-row-stripe.test.tsx`: formatting, countdown, over/unknown values, replica shapes, square fallback, lead selection, gateway order and timer-without-cloud versus CI cloud.

These are regression fixtures, not a native visual inspection, live service-health proof or ETA accuracy study.

## Applying

Use the complete replay recipe in `core/README.md`. Carry backend schema/outcome, replica marker and frontend changes together; replacing only renderer types cannot supply missing replica data. Rebuild Desktop through the normal release process after applying. No Desktop timeout or separately published lanes stale-while-revalidate plugin change is part of these ordinals.

## Gotchas

An estimate is not a deadline or admission/release signal. Remote CI/deploy stays unknown without an owner watch; timer health describes a declared clock, not an external service. Unknown history is intentional. Countdown uses the client clock, so clock skew affects presentation. Capped card detail cannot enumerate every wait counted in a row.

## plugin?

Core and built-in Desktop changes are required for canonical outcome and snapshot carriage. The touched built-in Kanban frontend module is not a separately installable plugin implementing this feature.

# Session outcome: silent unterminated subagent is lost, not done

Patches: 2834, 2835.

- **2834** (`7bce495bf6`, fix(outcome): a silent unterminated subagent is lost, not gone: no derived done (false green))
- **2835** (`7bc6ceba39`, fix(outcome): lost-subagent accounting spans the compression lineage)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- A child whose session row never ended dropped out of the parent's waits after `SUBAGENT_LIVE_WINDOW_S` (6 h), so a later closing-looking reply derived `done`. Such a child started within `SUBAGENT_LOST_BOUND_S` (7 d) and active after the chat's latest declared outcome is now lost: `unresolved{reason: subagent_lost, on:[{kind: subagent, state: lost}]}`, ranked after open decisions, lost requests and live waits and ahead of an earlier declared wait/done. A declaration made after its last activity accounts for it; derived outcomes keep `last_declared_at`. Desktop gets the reason type and tooltip copy.
- 2835: accounting is read over the compression lineage (latest across ancestors) and carried as `last_declared_at` on the new tip so compressing an accounted chat does not flip it back.

## How it works

- `hermes_state_outcome.py` (`_lost_subagent_items`, `_accounted_at`, `_derive_kept_receipt`); desktop `types/session-outcome.ts`, `i18n/en_session_row_status.ts`.

## Code

- `apps/desktop/src/i18n/en_session_row_status.ts`
- `apps/desktop/src/types/session-outcome.ts`
- `hermes_state_outcome.py`

## Tests

- `tests/hermes_state/test_session_outcome_subagent_lost.py` (stale child with closing reply lost; model-config delegate; started_at fallback; fresh waits; older than bound ignored; ended child ignored; declared done accounts; accounting survives later lapse; compression keeps ancestor accounting), `apps/desktop/src/lib/session-outcome.test.ts`.

Test files touched by these patches:

- `apps/desktop/src/lib/session-outcome.test.ts`
- `tests/hermes_state/test_session_outcome_subagent_lost.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Only locale en copy was added for the tooltip.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: core state code plus desktop app.

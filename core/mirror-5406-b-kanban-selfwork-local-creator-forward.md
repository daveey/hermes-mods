# Kanban selfwork forward: authorize origin-local creators without remote obligations, keep vetoes

Mirror entries 5174-5178.

## Behavior

- `receive()` used to require an accepted remote obligation, which source-only local tasks never have, so local detector self-work was refused as creator-unauthorized. Now an accepted obligation is tried first for every creator (including same-gateway); otherwise live origin-local task/run/session proof plus a fresh Board lease authorizes.
- A matching released, corrupt or unacknowledged obligation is a veto, not absence; local fallback is allowed only for genuine absence, rechecked inside admission for live and historical recovery. A newly accepted obligation appearing between proofs refuses rather than mixing evidence.
- Historical same-id repair of a terminal rejected-unauthorized request goes through the origin journal and historical local provenance, and requires the sender to have held the run while the refusal was live (never-holder, stale journal after stop, journal before run start, other failure codes and mismatched envelopes are refused).
- 5178: recovery uses the exact portable run's Board holder receipt (nullable SQL run id) rather than the latest local lock bookkeeping event; wrong-holder veto and conflicting payload run UIDs are kept; legacy integer-bound receipts still work.
- 5177 is test-only: provenance and journal guards are unmasked by seeding the original holder and composing real outbound prepare/send with receive.

## How it works

- `hermes_cli/kanban_selfwork_forward.py` only.

## Code

- `hermes_cli/kanban_selfwork_forward.py`

## Tests

Source tests (not executed in this mirror pass): `tests/hermes_cli/test_kanban_selfwork_forward.py` (origin-local live creator, negatives, obligation path unchanged, same-gateway obligation, never-holder and stale-journal repair refusals, invalid obligation veto, veto recheck, outbound prepare to receiver composition, provenance recheck before admission) and `test_kanban_selfwork_forward_claims.py` (`test_board_claim_survives_local_lock_receipt`, `test_wrong_board_authority_refused`, `test_latest_exact_run_holder_vetoes_earlier_claim`). A real cross-gateway courier is untested.

## Applying

These entries are part of the complete ordered mirror series. Apply them only through the segmented Applying recipe in the core mods [README](README.md): the full ordered patch list with `git am -3 --empty=keep`, the replay bridges, and the mandatory escape repair that README names. They are not independent patches and this document is not a standalone certification: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Authorization is deliberately conjunctive; an ambiguous obligation blocks local proof.

## Why core instead of a plugin

The forward contract is the core kanban authorization of cross-gateway work creation.

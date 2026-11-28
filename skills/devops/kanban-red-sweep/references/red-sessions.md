# Red sessions with nothing to click

A session turns red when a linked card sits in triage past the overdue-triage timeout, with no `human_request`.
Most such cards are deliberate holds (owner-retained, spend/scope stop, lane_gate, provider safety refusal), so
the panel shows red with no ask.

## Clear them through their owning chats

One `cross_session send` per red session (batch the sends in one turn), naming that session's cards and kinds:

1. Re-read each card live (`kanban_show`).
2. If an agent can move it: release under the existing mandate, `lanes admit` for lane_gate, promote, complete
   with verified evidence, or archive as superseded. Comment the evidence; one line back.
3. Otherwise file ONE blocked `kanban_create` ask per generate-unblock-card (safety-refused cards: no release
   option; archive/keep only).
4. Reply with the card ids moved or filed.

Owning chats hold the context, so most clear their cards within minutes; a central sweep re-deriving each hold is
slower and files duplicate asks. Expect some owners to keep cards parked on purpose and file nothing: report those
as "kept parked, nothing needs you" rather than re-asking.

## Releases only a person can make

`hermes kanban release` refusing `allowance_exhausted` (or `allowance_in_force`) needs a human writer. Agents,
the owning chat and `run_in_user_terminal` windows all count as agent-origin and are refused. Don't route David
to type a release command or card id; see generate-unblock-card step 1.7 for the button that actually releases.

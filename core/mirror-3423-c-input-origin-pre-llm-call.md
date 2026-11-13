# input_origin for pre_llm_call plugins

Patches: **3058, 3081, 3091, 3097**.

## Behavior
- `pre_llm_call` plugins receive `input_origin`: `agent`, `human`, or empty/unknown, per client submit. Agent transports, agent-created sessions, relayed or hosted submits, cross-session sends and Bot Mode room-plumbing sessions classify as `agent`; a typed Desktop submit is `human`. A merely hidden session is no authorship signal. CLI and gateway platforms leave it empty.
- The origin is forwarded to compute-host turns.
- A stored room-plumbing row is read with a narrow three-column query per submit.

## How it works
- 3058 adds `human_input.input_origin()` and passes it through `prompt.submit` to the agent and into `pre_llm_call` (signature-filtered dispatch).
- 3081 keys on room plumbing instead of hidden and forwards origin through the compute-host bridge.
- 3091 pins the plumbing rule to the resume rule with a parity test and narrows the row read.
- 3097 makes `turn_outcome.turn_sid` public.

## Code
- `tui_gateway/human_input.py`
- `tui_gateway/methods_prompt.py`
- `tui_gateway/methods_prompt_admission.py`
- `tui_gateway/prompt_turn.py`
- `tui_gateway/compute_host.py`
- `tui_gateway/compute_host_bridge.py`
- `tui_gateway/turn_outcome.py`
- `tui_gateway/session_auto_continue.py`
- `agent/turn_context.py`
- `website/docs/user-guide/features/hooks.md`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/agent/test_turn_context.py`: `test_pre_llm_call_receives_the_turn_input_origin`.
- `tests/tui_gateway/test_human_input.py`: `test_input_origin_human_for_a_typed_desktop_submit`, `test_input_origin_agent_for_an_agent_transport`, `test_input_origin_agent_for_agent_created_or_hidden_sessions`, `test_input_origin_agent_for_a_cross_session_send`, `test_input_origin_unknown_otherwise`, `test_input_origin_never_raises`, `test_hidden_alone_is_no_authorship_signal`, `test_input_origin_human_in_a_stored_hidden_bot_chat`, `test_row_is_room_plumbing_matches_the_resume_rule`, `test_input_origin_reads_only_the_plumbing_columns` (list is partial).
- `tests/tui_gateway/test_compute_host_turn_protocol.py`: `test_turn_start_runs_the_turn_with_the_forwarded_input_origin`.
- The `turn_sid` rename (3097) has no dedicated test.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
The room-plumbing rule is copied from upstream resume logic and kept in sync by a parity test.

# Paired-device availability waits expose owner attention

Patch: **5912**.

## Behavior

Legacy scheduled paired-device availability/visibility probes display human attention with an `owner_step` instead of hiding behind an external producer. The instruction asks the user to confirm device access or report a wrong check; it does not claim unlocking or proximity will fix a transport.

New agent-created waits using these recognized probes refuse scheduling/continuation with `human_wait` and direct the caller to a validated `needs_input` human request with a physical step and `resolved_check`. System requeues remain exempt from this new-agent admission check.

## How it works

`owner_device_step` uses `shlex.split` and a narrow script-basename pattern for paired phone/tablet/device available/visibility probes. It accepts direct execution or a recognized Python interpreter with supported flags. It does not execute probes, inspect arbitrary scripts or treat URLs/passive capture producers as device-access proof.

Wait derivation adds `wait_state: human`, `waiting_on`, `owner_step` and non-agent-owned certainty only for the applicable scheduled legacy wait. An already validated human ask takes precedence. Other statuses do not resurrect old device waits. The resolver checks recognized owner-device probes before board reads/writes for non-system requests.

## Code

- `hermes_cli/kanban_wait_info.py`: conservative recognizer and human-attention classification, including replica derivation.
- `hermes_cli/kanban_awaiting.py`: pre-admission refusal for new agent parks.

## Tests

**Source regression coverage; not executed in this mirror pass.**

`tests/hermes_cli/test_kanban_owner_device_wait.py` covers immutable legacy classification, replica parity, known invocation forms, passive/unrelated negative controls, refusal before board access, atomic real schedule/continue rejection without persisted wake state, system requeue exemption, validated-ask precedence and old-state suppression. This does not verify real device visibility or a user's physical action.

## Applying

Use the snapshot replay recipe in `core/README.md` with prior wait-info and scheduling contracts. Port both display classification and admission enforcement: display alone would leave new hidden human asks possible.

## Gotchas

Attention is not approval, consent or validated completion. Recognition is intentionally narrow rather than semantic analysis of shell code. A device being unlocked does not establish that its transport works. Preserve explicit human-request validation for new asks; the compatibility classifier is not a shortcut around it.

## plugin?

Core is required at the scheduling and canonical wait-info boundaries. Plugins may render the owner step, but must not execute the probe or infer consent while deriving attention.

# Kanban notices: review-contract refusal cause and last checkpoint in ready-wait

Patches: 2794, 2797.

- **2794** (`ad4c73fc7b`, kanban_complete: the review-contract refusal names its cause on the core-pull path)
- **2797** (`83e888749b`, kanban: ready-wait notices name the card's last checkpoint (kanban-3debc730ab))

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- 2794: `refusal()` opened with "this card states the ship review contract" on both gate paths. On the core-pull path it now opens with "this card reviews a core-fork pull (<url>)", naming the first core-fork pull in the body (new `core_pull` helper; `names_core_pull` wraps it).
- 2797: a ready card whose latest run parked (continued/blocked/scheduled) now carries that run's checkpoint (first line, 200 chars, end time) in `chat_wait_unclaimed` and `dependency_parent_unclaimed` payloads; the notice and the `kanban_show` ready hint say `Last checkpoint (HH:MMZ): "..."` and tell the reader to resume or finish what it records before re-placing or duplicating.

## How it works

- `hermes_cli/kanban_review_contract.py`; `hermes_cli/kanban_last_checkpoint.py` (new), `kanban_chat_wait.py`, `kanban_parent_unclaimed.py`.

## Code

- `hermes_cli/kanban_review_contract.py`
- `hermes_cli/kanban_chat_wait.py`
- `hermes_cli/kanban_last_checkpoint.py`
- `hermes_cli/kanban_parent_unclaimed.py`

## Tests

- `tests/tools/test_kanban_review_contract.py` (`test_refusal_names_its_cause_on_each_path`, `test_core_pull_is_the_first_core_fork_pull`); `tests/hermes_cli/test_kanban_ready_wait_checkpoint.py` (chat-wait and parent-unclaimed notices, show hint, no-prior-run card, old payload renders unchanged).

Test files touched by these patches:

- `tests/tools/test_kanban_review_contract.py`
- `tests/hermes_cli/test_kanban_ready_wait_checkpoint.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Old payloads without the field render unchanged.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.

# Owner-terminal ask lint exception

Patches **2076, 2077, 2078**.

## Behavior

- `kanban_block` rejects a human ask that merely relays a message to an agent (`agent_relay_ask`). A host-only step that only the owner's own chat can open (a terminal flow) was caught by that lint even after both agent routes (`run_in_user_terminal`, `cross_session` send) were refused to a Kanban caller.
- Final behavior (2078): the exception applies **only** when `interaction` is `user_terminal`, `action` matches the fixed sentence "From your own Hermes chat open|start the owner-terminal flow for task t_<8 or 16 hex>. Unblock = resume verification; archive = abandon reconciliation.", `why_human` equals the fixed host-only-boundary reason, and `evidence` equals the fixed two-refusal text. Anything else is still a relay. The exception is presentation-lint only; it grants no execution authority and the runtime owner-only gates are unchanged.
- The standing-ask sweep applies the same predicate, so an attested ask is not later flagged.

## How it works

Three source commits form one design arc. 2076 added a regex check for refusal mentions in free-text evidence. 2077 narrowed it to explicit host-only initiation with positive per-tool refusal clauses. 2078 replaced the inference with a closed template compared with `fullmatch`/equality after whitespace normalisation, because free-text "refused" cannot distinguish negations or hypotheticals. `agent_relay_ask_error(action, human_request=None)` now returns `None` when `owner_only_action_evidenced(human_request)` is true; the typed refusal text teaches the exact template.

## Code

- `hermes_cli/kanban_block_policy.py`: `owner_only_action_evidenced`, `agent_relay_ask_error`, `user_ask_lint_error`, `standing_ask_error`.

## Tests

**Source regression coverage; not rerun in this documentation pass.** `tests/tools/test_kanban_owner_only_ask.py`: an ask with the attested fields passes lint; a relay ask without them is refused (parameterised overrides of each field); a non-dict request is not owner-only; the `kanban_block` entry accepts the attested ask and still refuses a true relay; the standing-ask sweep keeps an attested ask. The test file was edited by 2077 and 2078 to match the narrowing.

## Applying

Apply ordinal(s) 2076, 2077, 2078 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. The intermediate 2076/2077 regex designs are superseded by 2078; reimplementing from scratch should use only the closed template. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

- The template is exact text; a paraphrase is a relay. Extra detail belongs in scope/reason fields.
- Evidence is a description, not an authenticated receipt; do not treat the lint pass as proof the tools were refused.
- Related: [kanban-ownerless-blocks.md](kanban-ownerless-blocks.md), [worker-capability-relay.md](worker-capability-relay.md).

## Could this be a plugin?

No. It edits the core block-policy validator that every `kanban_block` call passes through.

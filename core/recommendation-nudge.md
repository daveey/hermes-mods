# Recommendation nudge on needs-input blocks

Patches: **2518–2523**.

## Behavior

- After a *successful* `kanban_block` whose `human_request` has a choice explicitly labelled `(recommended)` or `[recommended]` (case-insensitive) and an `interaction` of `reply` or `approval`, the block result may gain an advisory `warning` string. Block admission and card state are unchanged: the card is blocked exactly as before and the run still ends.
- The warning is conditional, not a verdict. It reminds the worker that, under the user's standing rule, a decision is the worker's to apply (not ask) only when it is in scope, reversible and within the existing review and landing gates, and crosses none of: spend beyond standing limits, permissions or credentials, other people or outward communication, safety or privacy consent, production systems or devices, hard-to-reverse effects, or a real toss-up. If any applies, the worker should ignore the note; it grants no permission. Otherwise it says not to apply the option now (the block stands) but to comment the recommended pick and how to reverse it, and next time to apply and comment instead of blocking. The text opens "You marked a recommended option. Under David's standing rule ..." (the one place the rule's owner is named, quoted verbatim).
- The warning is suppressed (result unchanged) when: the interaction is `physical`, `user_terminal` or `external`; no choice carries an explicit positive marker (`(not recommended)` or "is not recommended" does not count); neither the `action` nor the recommended choice's label/value carries a code/scope signal (PR, commit, test, fixture, module, helper, shim, rename, refactor, timeout, retry/backoff, config, flag, pre/postmerge, lint, `:port`, worktree, function, code, docs, schema, regex, parser, a `.py` path, a branch other than branch protection); or a boundary term or named person appears anywhere in `action`, `scope`, `why_human`, `evidence`, or any choice's label/value. The scope field alone never qualifies an ask.
- The worker prompt (`KANBAN_GUIDANCE`, step 4) now tells workers to apply an evidence-backed in-gate recommendation, comment the pick and its reversal, and ask only for real money above standing envelopes, permissions or credentials, other people, safety or privacy consent, hard-to-reverse effects, or genuine toss-ups. It states this never bypasses review or landing gates.

## How it works

`recommended_choice_warning(human_request)` in `hermes_cli/kanban_block_policy.py` returns `{}` or `{"warning": RECOMMENDATION_WARNING}`. It tolerates malformed input (non-dict request, non-list choices, non-dict choice entries). Gating order: interaction, explicit positive marker, `_RECOMMENDATION_IN_GATE` on the action plus the recommended choice, then `_RECOMMENDATION_BOUNDARY` (money, credentials, destructive, outward communication, production/devices, physical terms) and `_RECOMMENDATION_NAMED_PERSON` (ask/tell/ping/DM a name, possessive names, `@handles`) as a veto over all text fields. Both patterns are a noise filter, not an authorization classifier; the safety comes from the warning's conditional wording. Nothing is stripped before the veto, and the ask template's own verbs ("reply", "drop") are not veto terms.

`_handle_block` in `tools/kanban_tools.py` merges the result into the landed-block response (`_ok_landed(..., **warning)`) after the block lands. The helper `_escalated` moved to `tools/kanban_tools_park_args.py` to keep the facade under its size cap (behavior unchanged). Related: [kanban-ask-premise-probe-warning-2491.md](kanban-ask-premise-probe-warning-2491.md) is another non-fatal block-time warning.

## Code

`hermes_cli/kanban_block_policy.py`, `tools/kanban_tools.py`, `tools/kanban_tools_park_args.py`, `agent/prompt_builder.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tools/test_kanban_user_ask_lint.py`. Entry paths: the `kanban_block` tool handler (`kanban_tools._handle_block`) for the acceptance matrix (warning on an in-gate recommended reply; none for spend, permission, consent, physical, no recommended choice, privacy scope, reviewer, delete), checking the block lands and the card is `blocked`; the policy function `recommended_choice_warning` directly for malformed input, boundary terms in every field, positive-marker rules, asks only the user can decide (each run under two scopes), reviewer-probe fixtures, in-gate controls and the warning wording; the `KANBAN_GUIDANCE` constant for the prompt phrases. CLI, REST and Desktop block paths are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Advisory only: it never applies a decision, changes admission, or grants permission; a miss or a misfire costs a comment, not an action.
- The vocabulary lists are English-only heuristics and will miss unusual asks; that is why the warning text tells the worker to ignore it when a boundary applies.
- The in-gate signal list is deliberately narrow (fail-safe): a recommended ask with no code signal gets no note.

## Plugin

Core change; no plugin required.

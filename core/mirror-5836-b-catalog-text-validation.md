# Catalog-aware rendered mandate validation

Source patch ordinals: 5621–5623, 5630–5631.

## Behavior

Kanban text validation identifies concrete catalog model/provider mandates without treating incidental tool names, route sentinels, completed-review attribution or removed requirements as active routing policy. Refusals report the original offending source line.

## How it works

Model identities come from the offline static catalog plus catalog-backed family aliases, not broad vendor-prefix guesses; `default` and `auto` are excluded. Directive patterns bind an obligation to its route slot. Negative/removal phrases are masked narrowly so an appended positive mandate still refuses. Rendered inline formatting is inspected without rewriting stored bytes. Implementer attribution comments are removed only through their closing delimiter, leaving following text checked. Provider-only obligations get a similarly bounded route-slot rule. Guidance explains the lossless historical JSON-evidence form.

## Code

- `hermes_cli/kanban_model_neutral.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/hermes_cli/test_kanban_model_neutral_review41.py` covers catalog identities, ordinary prose/subjects and exact-line diagnostics. `test_kanban_model_neutral_sentinels.py` checks sentinel references versus real appended mandates. `test_kanban_model_neutral_review44.py` covers negation, evidence resubmission, active suffixes and original-line preservation. `test_kanban_text_review58.py` exercises rendered/model/provider restrictions and unchanged controls across source mutation entries.

## Applying

Apply as follow-ups to the shared model-neutral validator and evidence format; all common mutation consumers inherit the stricter parser. No online catalog or credential lookup is needed for text validation. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

This is a bounded text policy, not a natural-language proof system or live entitlement check. Historical wrapping is for superseded evidence only. Markdown formatting and provenance comments must not mask a trailing mandate.

## plugin?

Core pure validation, available to plugins only through its named public validation exports.

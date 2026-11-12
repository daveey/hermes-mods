# Premise-probe warning for human asks

Patches: **2451, 2452**.

## Behavior

When a worker blocks a card on a human (`kanban_block` with `human_request`) and the ask names a machine-checkable premise but carries no `resolved_check`, the block still succeeds and the tool result gains a `resolved_check_warning` string. It suggests a probe the worker should review and attach so a changed premise resumes the card automatically. Detected premises: a GitHub pull-request URL (suggests a `gh pr view ... --json state` probe: exit 0 for MERGED/CLOSED, 1 for OPEN, 2 otherwise), an absolute filesystem path (suggests `test -e <path>`, or its negation), and a build/version mention (suggests reusing the existing version read; a `version read: \`cmd\`` hint in the ask is quoted back). Asks that already have a `resolved_check`, purely physical asks, and asks with no such premise get no warning. Suggestions are never executed.

Patch 2452 only adds a contributor attribution file, published under the public path `contributors/emails/ci-worker@example.com`, so the CI attribution check accepts the previous commit's author; it contains one line mapping to the contributor's public handle and has no runtime effect.

## How it works

`kanban_ask_premise.warning_fields(request)` scans the `action` and `evidence` fields with regexes for PR URLs, quoted or bare absolute paths, and build/version strings, and returns `{"resolved_check_warning": ...}` or `{}`. `tools/kanban_tools._handle_block` merges that dict into the `extra` fields of its success result, after the block has landed. It follows the lint in [kanban-user-ask-lint.md](kanban-user-ask-lint.md) and complements the [resolved check](kanban-ask-resolved-check.md).

## Code

`hermes_cli/kanban_ask_premise.py`, `tools/kanban_tools.py`, `contributors/emails/ci-worker@example.com`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tools/test_kanban_user_ask_lint.py`. Covered entry path: the `kanban_block` tool handler (`_handle_block`) for PR, path and build premises, no warning for physical or already-probed asks, quoted paths with spaces, and `warning_fields` directly for empty input. The CLI block command and REST paths are untested for this warning; `kanban_create` is not changed and untested. Patch 2452 has no tests.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Advisory only: the ask is never refused and the card is blocked either way.
- A closed PR or a missing path may call for replanning rather than completion; the worker must confirm target, resolution condition and probe host.
- Regex-based: a PR URL followed directly by other word characters (for example `.../pull/42oops`) is not matched, and any absolute-looking path in the text can trigger a path suggestion.

## Plugin

Core change; no plugin required.

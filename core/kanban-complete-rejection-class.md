# Kanban: a stable `rejection` class on every `kanban_complete` refusal (patch 0140)

Patch:

- `patches/0140-feat-kanban-stable-rejection-class-on-every-kanban_c.patch`: `tools/kanban_tools.py` and a new
  `tests/tools/test_kanban_complete_rejection_class.py`.

What you see: when `kanban_complete` refuses, the tool error JSON carries a `rejection` field next to `error`,
for example `{"error": "kanban_complete blocked: created_cards ...", "rejection": "phantom_cards"}`. Digests and
board analytics can count failed completions by cause instead of grepping prose that changes between releases.
A successful completion has no `rejection` key. Nothing else about the tool changes: the messages, the order of
the checks and what gets written are the same as before.

## Behavior

Every `kanban_complete` error has exactly one class from `COMPLETE_REJECTION_CLASSES`:

| Class | When |
|---|---|
| `goal_judge` | The goal-mode completion judge said the card is not done (`continue`) or `blocked` |
| `phantom_cards` | `created_cards` names ids this worker did not create (`HallucinatedCardsError`) |
| `artifacts` | Declared artifacts could not be preserved (`ArtifactPreservationError`) |
| `empty` | No `summary`/`result`, or an evidence-free completion (`EmptyCompletionError`) |
| `live_claim` | An env-less caller (orchestrator, another session) on a card a live dispatcher worker owns |
| `stale_run` | The run was superseded, the card is already terminal, or the id is unknown |
| `parents` | Unsatisfied parent dependencies |
| `not_owner` | The worker guard refused: a delegate child, another task's id, an unbound run id |
| `invalid_args` | Unknown parameter, malformed `metadata`/`created_cards`/`artifacts`, any other untagged validation refusal |
| `error` | An unexpected exception (logged with a traceback, as before) |

The set is append-only: consumers key on these strings, so a class is never renamed.

Only `kanban_complete` is classified. The other Kanban tools (`kanban_block`, `kanban_create`, ...) return the
same errors as before with no `rejection` field.

## How it works

- `_Reject(message, rejection=None)` and `_check(cond, message, rejection=None)` take an optional class and put it
  into the `tool_error` payload. Each refusal site in `_handle_complete` and `_goal_gate` passes its class.
  The direct `tool_error(...)` returns for artifacts, live claim, phantom cards and empty completion pass
  `rejection=` too.
- `_kanban_handler(tool_name, classify_rejections=False)`: with the flag on (only `kanban_complete` sets it),
  the wrapper backfills a class on anything that slipped through untagged. An untagged `_Reject` or an error
  JSON string returned by the handler becomes `invalid_args`, and any other exception becomes `error`.
  `_tag_rejection(payload, cls)` never overwrites a class that is already set.
- The worker guard (`_worker_guard`) is shared with the other tools, so `_handle_complete` catches its
  `_Reject` and re-raises it with the same message as `not_owner`.

## Code

```python
COMPLETE_REJECTION_CLASSES = frozenset({
    "goal_judge", "phantom_cards", "artifacts", "empty", "live_claim",
    "stale_run", "parents", "not_owner", "invalid_args", "error",
})

@_kanban_handler("kanban_complete", classify_rejections=True)
def _handle_complete(args: dict, **kw) -> str:
    try:
        tid = _worker_guard("kanban_complete", args)
    except _Reject as e:
        raise _Reject(json.loads(e.args[0])["error"], e.rejection or "not_owner") from None
    ...
    _check(summary or result, "provide at least one of: summary (preferred), result", "empty")
```

## Tests

`tests/tools/test_kanban_complete_rejection_class.py` (11 tests) drives `_handle_complete` against a real
temp board. Each test asserts that the class is in `COMPLETE_REJECTION_CLASSES` and is the expected one:
phantom cards, empty, invalid args (an unknown key and a non-dict `metadata`), not owner (another task id),
stale run (a mismatched run id), parents (parent reopened after the claim), goal judge (judge stubbed to
`continue`), live claim and artifacts (`complete_task` raising `LiveClaimError` / `ArtifactPreservationError`),
error (`complete_task` raising `RuntimeError`). A successful completion has no `rejection` key.

```sh
scripts/run_tests.sh tests/tools/test_kanban_complete_rejection_class.py
```

Passed on the full 0001–0143 series at merge-base `4957bbd0a0` (Oct 19, 2026 PT). Untested: the CLI
`hermes kanban complete` path, which does not go through the tool handler and has no `rejection` field.

## Applying

`git am -3 --empty=keep core/patches/0140-*.patch`. Only `tools/kanban_tools.py` changes, but its hunk context includes
`_goal_gate` and the `kanban_complete` refusals from earlier Kanban patches (goal judge, phantom cards, empty
completions). Take it after those. 0141 edits the same file right after this patch's hunks, so apply 0140 first.

## Gotchas

- `invalid_args` is also the catch-all for any untagged `_Reject` inside `_handle_complete`. A new refusal added
  without a class shows up there, not as `error`. Tag new refusals explicitly.
- The class describes why the call was refused, not the card's state. A `stale_run` card may already be done.

Plugin? No. A `transform_tool_result` hook could add the field by matching message text, but that is the prose
match this patch removes. The classes have to come from the code that knows the cause.

# Release mandate and typed stop recovery

**Scope:** Worker context mandate, human instructions, uid binding, recovery advice.

**Source ordinals:** 6489, 6497, 6498, 6500, 6501, 6543, 6544, 6545

## Behavior

- A released card's worker context leads with a 'Release mandate' section (the release newer than the card's newest stop), above checkpoints and attempts, for budget and capability stops as well; older agent-written stop rules and earlier stop reasons are framed as history, and scope stops name an allowed stop.
- Human comments older than the thread tail (newest 10, 1 KiB each) render in a separate instructions section, restricted to comments authored by a person (human writer kind, relayed human or recorded decision); agent notes, operator verbs and system writers are excluded. The section appears only on released or scope-stopped cards and is absent on ordinary cards. The mandate section is hidden when a newer stop or an execution restriction exists.
- A `scope_stopped` retain now requires a `stop_on` category from a fixed list (provider safety refusal, needs the owner physically, same error three times, run budget, owner instruction, card safety exclusion, execution restricted); it is folded into the summary as `[stop_on=<category>]` (2,000-character cap) and rejected for other reasons. A missing or invalid category is refused without ending the run, and the prompt/tool text says findings, red CI and sandbox refusals are work, not stops.
- Release ordering uses creation order. The read-first check binds unread human comments by comment uid (falling back to id only for uid-less own-board events), and the refusal names the missing uid and the human route.
- Release authority failures now show the typed stop and the fully-qualified expected owner via a read-only advice module, without changing who may release.

## How it works

- `kanban_db_context.py` renders sections; `kanban_release.py` holds `by_person`, `_newest_stop`, `_release_event` and read-first logic; `kanban_stop_recovery.py` builds advice; `agent/prompt_builder.py` and `kanban_stranded_brief.py` carry wording.

## Code

Files touched by the series (paths relative to the source tree):

- `agent/prompt_builder.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_context.py`
- `hermes_cli/kanban_rejected_retain_source.py`
- `hermes_cli/kanban_release.py`
- `hermes_cli/kanban_retain.py`
- `hermes_cli/kanban_stop_recovery.py`
- `hermes_cli/kanban_stranded_brief.py`
- `tools/kanban_retain_tools.py`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/test_kanban_retain_local.py`
- `tests/hermes_cli/test_kanban_retain_process.py`
- `tests/hermes_cli/test_kanban_stop_recovery.py`
- `tests/hermes_cli/test_kanban_worker_stop_rules.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6489-6545, 8 commits: 6489, 6497, 6498, 6500, 6501, 6543, 6544, 6545) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Comment ids from a synced comment are the writer board's local ids, which is why uid binding is used.
- Kind-less comments stay in the ordinary thread only.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Prompt context core.

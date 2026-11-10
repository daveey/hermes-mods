# Refuse linking under a never-finishing standing card

Patch **2092**.

## Behavior

`link_tasks` now refuses `parent -> child` when the parent is a standing card: not done/archived and either titled with a `Standing:` prefix or ended more than 5 runs as `scheduled` in the last 72 hours. The error says a wait on an event uses `kanban_schedule(wake_check=...)` and a wait on a person uses `kanban_block(kind='needs_input', human_request=...)`. Ordinary parents, old parks outside the window, and cycles (still refused) are unchanged. The `kanban_link` tool path surfaces the same refusal.

## How it works

`kanban_dependency_validation.check_link_parent` runs the cycle check then `kanban_standing_parent.standing_reason` (`REPARK_LIMIT=5`, `REPARK_WINDOW_S=72h`, `TITLE_PREFIX`), raising `standing_parent_error`.

## Code

- `hermes_cli/kanban_db.py`, `hermes_cli/kanban_dependency_validation.py`, `hermes_cli/kanban_standing_parent.py` (new).

## Tests

**Source regression coverage; not rerun in this documentation pass.** `tests/hermes_cli/test_kanban_standing_parent_link.py`: standing-title parent refused; frequently re-parked parent refused; normal parent links; old parks do not count; the `kanban_link` tool refuses. Creating a card with `parents=` at creation time is not covered here (untested).

## Applying

Apply ordinal(s) 2092 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. See [standing-instructions.md](standing-instructions.md) for the unrelated instruction feature of a similar name. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

Existing links under such a parent are not removed. The title check is prefix-only and case-insensitive.

## Could this be a plugin?

No. Link validation is inside the core Kanban database layer.

# Kanban-only skills follow the Kanban tool gate, not a bare board pin

Patches: 1682

- `patches/1682-fix-skills-a-bare-HERMES_KANBAN_BOARD-pin-no-longer-.patch`

What you see: skills that declare `environments: [kanban]` (review and worker-protocol skills) no longer appear in the
skill list or slash commands of an ordinary classic CLI / legacy TUI chat. They still appear for Kanban workers, for
sessions started on behalf of another gateway's chat, and for profiles that enable the `kanban` toolset.

## Behavior

- `agent/skill_utils.py::_detect_kanban` no longer treats `HERMES_KANBAN_BOARD` alone as a Kanban signal. Every classic
  CLI / legacy-TUI chat sets that variable at boot (`hermes_cli.main_tui_launch._pin_kanban_board_env`), so the old
  check listed Kanban-only skills in chats that never get the Kanban tools.
- The Kanban environment is now detected when any of these hold, matching `tools/kanban_tools.py::_visible`:
  1. `HERMES_KANBAN_TASK` is set **and** this execution owns the task
     (`agent.delegation_context.is_dispatcher_owned_worker_context()`; delegate children and in-process cron that
     inherit a worker's variables do not count). The dispatcher always sets the board together with the task.
  2. An ambient origin session exists (`hermes_cli.kanban_origin.ambient_origin_ref()`, e.g. `HERMES_ORIGIN_SESSION`).
  3. The profile's toolsets include `kanban` (`tools.kanban_tools._profile_has_kanban_toolset`).
- `_detect_environment("kanban")` and skill command discovery (`agent.skill_commands.get_skill_commands`) follow the
  same answer.

## How it works

The first branch now checks only `HERMES_KANBAN_TASK`, then the dispatcher-ownership check. A new middle branch calls
`ambient_origin_ref()` inside `try/except ImportError`, so a core without `hermes_cli.kanban_origin` falls through to the
profile-toolset branch. Detection results are cached in `_ENV_DETECT_CACHE`, as before.

## Code

`agent/skill_utils.py` (`_detect_kanban`).

## Tests

`tests/agent/test_skill_kanban_env_gate.py` (new). Each test runs in a temporary `HERMES_HOME` with one ordinary probe
skill and one `environments: [kanban]` probe skill:

- `test_bare_board_pin_hides_kanban_skills`: board only → `(False, False)`; `/review-probe` absent, `/ordinary-probe` present.
- `test_dispatcher_owned_worker_shows_kanban_skills`: task + board → detected; `/review-probe` listed.
- `test_non_owned_worker_env_hides_kanban_skills`: task + board inside `non_dispatcher_owned_context()` → not detected.
- `test_ambient_origin_session_shows_kanban_skills`: `HERMES_ORIGIN_SESSION` alone → detected.
- `test_profile_kanban_toolset_shows_kanban_skills`: `platform_toolsets.cli` includes `kanban` → detected and listed.
- `test_chat_boot_pin_lists_no_kanban_skill`: runs the real `_pin_kanban_board_env()`, confirms it sets the board to
  `default`, and that the Kanban probe skill is still not listed.

The mirror pass did not rerun these tests.

Untested: the `ImportError` fallback when `hermes_cli.kanban_origin` is missing; the Desktop/`hermes serve` skill
list (the tests cover `get_skill_commands` and the detectors, not each UI surface); cache behavior when environment
variables change within one process.

## Applying

Standalone on any core where `agent/skill_utils.py::_detect_kanban` has the `HERMES_KANBAN_TASK or
HERMES_KANBAN_BOARD` check and `hermes_cli.kanban_origin.ambient_origin_ref` exists. Apply in series order.

## Gotchas

- A script that sets only `HERMES_KANBAN_BOARD` to act as a worker no longer sees Kanban-only skills; set
  `HERMES_KANBAN_TASK` (dispatcher-owned) or enable the `kanban` toolset for that profile.
- Already-running sessions keep their cached detection until restart.

## Plugin?

No. Skill environment detection is core skill discovery; plugins cannot change which built-in skill environments
apply.

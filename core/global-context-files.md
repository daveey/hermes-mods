# Global context files: user-level rules in every session, whatever its cwd

Patch: `patches/0552-feat-prompt-agent.global_context_files-user-level-ru.patch`

Upstream loads `AGENTS.md` only along the git-root → cwd chain. A user-level rules file such as `~/AGENTS.md`
therefore reaches only sessions whose cwd is `~` (or a repo with no AGENTS.md of its own). Kanban workers run in
scratch dirs, cron jobs in project dirs or none, Desktop chats in project folders, so most sessions never see it.
Measured on one MacBook over 24 h before this patch: 0/366 Kanban workers, 0/81 cron runs and 11/20 Desktop chats
carried the rules.

## Behavior

- New config key `agent.global_context_files` (list of paths, `~` expanded; a single string is accepted). Default
  `[]` = upstream behavior.
- Each readable file is loaded for every session of that profile, as its own section labelled
  `## <path> (global)`, ahead of the project chain (`.hermes.md` / `AGENTS.md` / `CLAUDE.md` / `.cursorrules`) and
  the project-context SOUL.md section. In the default prompt SOUL.md is the identity slot, so the global files follow
  it.
- A file whose content equals a project file that actually loads (cwd == `~`) is loaded once, as the project file.
  Two global entries with the same content load once.
- The same prompt-injection scan and per-file truncation cap as project context files apply.
- Missing or unreadable paths are skipped and logged at INFO (`global context file <path> skipped: ...`).
- Cron jobs without a workdir (`skip_context_files=True` with `load_soul_identity=True`) get the global files only,
  no project files. Sessions that skip context entirely (no identity slot) get nothing, as before.
- When the cwd falls back to the Hermes install tree (project discovery suppressed), the global files still load.
- The `/context` manifest (`agent/context_file_sources.py`) lists global rows first with status `loaded`,
  `truncated`, `flagged`, `duplicate` (same content as a loaded project file) or `missing`.

## How it works

`global_context_file_paths(home)` reads the key straight from that profile's `config.yaml` with
`utils.load_yaml_file_readonly` (signature-cached; no full config merge on the prompt path, and the right profile
even on a thread that lost the `HERMES_HOME` ContextVar). `_global_context_candidates` reads each file with the
existing timeout reader. `_loaded_project_contents(cwd)` replays the "first context type with a non-empty file
wins" ladder to collect what the project chain will load, and `_load_global_context` renders one
`_context_section` per file whose content is not in that set. `build_context_files_prompt` prepends those sections.
`build_global_context_prompt` is the globals-only variant that `agent/system_prompt._context_files_part` uses for
cron's identity-only mode.

## Code

- `agent/prompt_builder.py`: `global_context_file_paths`, `_global_context_candidates`, `_loaded_project_contents`,
  `_load_global_context`, `build_global_context_prompt`, `build_context_files_prompt`.
- `agent/system_prompt.py`: `_context_files_part` (identity-only branch).
- `agent/context_file_sources.py`: `list_context_file_sources(include_project=...)`, `context_file_sources_for_agent`,
  `duplicate` / `missing` statuses.
- `hermes_cli/config_defaults.py`: `agent.global_context_files: []`.

## Tests

`tests/agent/test_global_context_files.py` (all fail without the patch: the new functions do not exist):

- `test_project_session_loads_global_file_before_project_chain`: cwd in a repo with its own AGENTS.md; both load,
  global first.
- `test_kanban_worker_in_scratch_dir_loads_global_file`: scratch cwd, no project files.
- `test_cron_without_workdir_loads_global_file_only`: `skip_context_files` + `load_soul_identity`.
- `test_cwd_equal_to_home_loads_the_file_once`: the global file is also the cwd's AGENTS.md; one copy.
- `test_unset_default_is_upstream_behaviour`: no key → prompt identical to upstream.
- `test_unreadable_and_missing_paths_are_skipped_with_a_log_line`.
- `test_global_file_honours_the_context_file_cap`.
- `test_ambient_profile_config_is_read`: no explicit home → `HERMES_HOME`'s config.
- `test_context_manifest_lists_global_rows`: `/context` rows and statuses.

Neighbours run green with the patch: `tests/agent/test_prompt_builder.py`, `test_system_prompt.py`,
`test_context_file_sources.py`, `tests/cron/test_cron_workdir.py`, `tests/gateway/test_skip_context_files_wiring.py`,
`tests/tui_gateway/test_session_agent_cwd.py`. (`tests/agent/test_compaction_prompt_rebuild.py` has 3 macOS-only
`/private/var` path failures on main before the patch; same count with it.)

## Applying

`git am core/patches/0552-*.patch`, then set the key, e.g.
`hermes config set agent.global_context_files '["~/AGENTS.md"]'` (it prints a false "not a recognized config key"
warning on cores without the patch's default). New sessions pick it up; open sessions keep their cached prompt.

## Gotchas

- The key is per profile. Bot profiles that should not follow your personal rules simply leave it unset.
- The file must exist on every host that runs the profile. A remote gateway needs its own copy (the author's hub
  installs `~/AGENTS.md` atomically from the private state repo on each config tick); a symlink into a directory
  that gets swapped would dangle for that moment.
- Replay/eval harnesses that rebuild the system prompt themselves must layer the key too (the `hermes-coach`
  skill's `coach_eval.py compose_system` does).
- No plugin alternative: plugin prompt sections are appended after the context tier and can't dedupe against the
  project chain.

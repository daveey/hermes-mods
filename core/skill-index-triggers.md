# Skill index rows show triggers: frontmatter

Patches: **1836** (`5fd59833c7`, `skill index: show triggers: frontmatter on index rows (skills.index_triggers, default on) [daveey]`), **1837** (`45f1c991e7`, `skill_manage: description convention names the user's words and triggers: frontmatter [daveey]`), **1838** (`681355340c`, `learn_prompt: description rule names the user's words and triggers: frontmatter [daveey]`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- A skill's `triggers: {asks: [up to 3 user phrasings], not_for: str}` frontmatter is appended to its `<available_skills>` row as ` Asks: "a"; "b"; "c". Not for: x.`; `skills.index_triggers: false` turns it off (default on) (1836).
- The setting is part of the in-process index cache key and the disk snapshot (v6), so toggling it or editing `triggers:` refreshes the index; v5 snapshots are rebuilt (1836).
- `skill_manage`'s description convention (1837) and the learn prompt's description rule (1838) tell agents to use the user's own words and the `triggers:` frontmatter.

## How it works

`agent/skill_utils.py` (`render_skill_triggers`, `extract_skill_description(with_triggers=None)` reading config) builds rows; `agent/prompt_builder.py` keys the LRU and snapshot on the setting; `hermes_cli/config_defaults.py` adds `skills.index_triggers`. Measured by the source author with hermes-mods `tools/skill-trigger-eval`: weakest-30 recall 0.67 → 0.83, full mean 0.921 → 0.947 (not rerun here).

Source commit notes (first paragraph of each message, privacy-normalized):

- 1836: A skill's triggers: mapping ({asks: [up to 3 user phrasings], not_for: str}) is appended to its <available_skills> row as ' Asks: "a"; "b"; "c". Not for: x.'. extract_skill_description() stays the one place rows are built (with_triggers=None reads config). The setting is part of the in-process cache key and recorded in the disk snapshot (v6), so toggling it or editing triggers: refreshes the index; v5 snapshots are rebuilt. Documented in creating-skills.md.
- 1837: skill_manage: description convention names the user's words and triggers: frontmatter [daveey]
- 1838: learn_prompt: description rule names the user's words and triggers: frontmatter [daveey]

## Code

`agent/prompt_builder.py`, `agent/skill_utils.py`, `hermes_cli/config_defaults.py`, `tools/skill_manager_tool.py`, `website/docs/developer-guide/creating-skills.md`, `agent/learn_prompt.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/agent/test_skill_index_triggers.py`: `test_triggers_render_after_the_clipped_description_by_default`, `test_config_off_hides_triggers`, `test_explicit_with_triggers_overrides_config`, `test_malformed_triggers_are_ignored_and_rows_stay_one_line`, `test_index_row_carries_triggers_and_a_frontmatter_edit_refreshes_it`, `test_toggling_the_config_rebuilds_cache_and_snapshot`, `test_pre_triggers_snapshot_is_rebuilt`
- `tests/fixtures/prompt_surface/surfaces.json`
- `tests/fixtures/prompt_surface/tools/skill_manage.json`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.

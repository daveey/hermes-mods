# Lean skills index and compact kanban_show for Kanban workers

Mirror snapshot 4352; ordinals 4213, 4214, 4228.

Entries:

- **4213**: Kanban workers: lean skills index + compact kanban_show payload
- **4214**: prompt_builder: move skills-index row labelling + render into prompt_builder_skills_index
- **4228**: compact skills index: strip triggers from plugin-registered skill rows too

## Behavior

A dispatcher-owned Kanban worker re-reads its system prompt on every API call and starts with `kanban_show`; both carried bytes it never uses.

- `skills.index_in_kanban_worker: full|compact|names` (default `compact`), applied only when a Kanban task is owned: `compact` drops each row's `Asks:`/`Not for:` trigger text, `names` lists skill names under each category without descriptions or triggers. No mode hides a skill name; chats keep the full index. 4228 extends the stripping to plugin-registered skill rows.
- `kanban_show` for a dispatcher worker without `verbose=true` drops `capacity_evidence` and `execution_observations` (keeping the one-line `capacity_note`), collapses consecutive same-run heartbeat/dispatch_skipped/respawn_guarded/wake_episode_evidence events into the newest one with `repeated`/`first_at`, strips `_`-prefixed payload keys and replaces texts of 64 bytes or more that `worker_context` already carries verbatim with "(in worker_context)". `verbose=true` and interactive callers see the old payload.
- 4214 moves skills-index row labelling and rendering into `prompt_builder_skills_index` as a pure refactor (re-exported) to keep `prompt_builder.py` under its line cap.

## How it works

- `agent/prompt_builder.py`, `prompt_builder_skills_index.py`, `skill_utils.py` (snapshot rows carry the exact `triggers` suffix; the cache key carries the mode), `agent/system_prompt.py`, `tools/kanban_show.py`, `tools/kanban_tools_schemas.py`; `website/docs/user-guide/configuration.md`.

## Code

Shipped non-test paths (union over these entries):

- `agent/prompt_builder.py`
- `agent/prompt_builder_skills_index.py`
- `agent/skill_utils.py`
- `agent/system_prompt.py`
- `tools/kanban_show.py`
- `tools/kanban_tools_schemas.py`
- `website/docs/user-guide/configuration.md`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/agent/test_skill_index_kanban_worker.py`
- `tests/tools/test_kanban_show_compact.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- Workers lose trigger phrasing in the index by default; set `skills.index_in_kanban_worker: full` to restore it. The byte savings in the commit message are measurements on one setup.

## Plugin?

None.

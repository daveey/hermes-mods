# Scratch path fences and lossless Git fallback archives

Mirror entries: 4534, 4551.

## Behavior

- **4534** — Scratch exemptions recognize only the core-created noindex sibling symlink shape, not arbitrary resolved symlink targets.
- **4551** — Unreadable nested Git metadata can fall back to a verified complete-filesystem archive without Git-shaped omissions.

## How it works

Delegation fencing retains the literal scratch exemption and additionally accepts descendants of its `.noindex` sibling only when scratch is a symlink whose raw target is exactly that sibling name or absolute sibling path. The scratch directory itself and pinned DB/legacy/delegate exclusions retain their fences; readlink failure grants nothing extra.

Archive preparation distinguishes Git interpretation failures from archive writes or verification failures. On supported inspection errors it discards all unpublished optional Git output and omission plans, including earlier healthy-repository optimizations, then builds the ordinary full-byte payload and records `git-fallback.json`. No source bytes are deleted by fallback preparation; existing durability, detach and payload-verification guards still govern eventual collection.

## Code

- `agent/delegation_context.py`
- `hermes_cli/kanban_scratch_archive.py`
- `hermes_cli/kanban_scratch_archive_fallback.py`

## Tests

Source path tests cover relative/absolute recognized links, nonlinks, arbitrary targets, unreadable links and unchanged exclusions. Archive tests cover malformed nested metadata, missing objects/alternates, realistic unreadable repositories, removal of earlier omissions/partial Git output, byte retention and subsequent GC, payload/detach guards and external-gitdir refusal. No archive/GC or filesystem fence probe was newly executed here.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/hermes_cli/test_kanban_scratch_archive_shapes.py`
- `tests/hermes_cli/test_kanban_scratch_git_fallback.py`
- `tests/tools/test_kanban_tools_scratch_home_fence.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

Fallback preserves bytes that exist; it cannot restore missing Git objects. External gitdirs are not self-contained and still refuse filesystem fallback. Payload/discovery/durability/verification errors are not reclassified as Git-inspection failures. Never broaden the path exemption by resolving arbitrary links.

# Kanban scratch archive: foreign ownership, retention and promisor clones

Patches: 5966, 5967, 5968, 5969, 5970, 5971, 5980, 5981, 5982, 5983, 5984.

## Behavior

- Device nodes are recorded in the archive manifest like FIFOs and sockets (type, mode, rdev) with no tar member, instead of refusing the whole workspace.
- Root-owned or otherwise foreign-owned entries are adopted before archiving: with passwordless privilege escalation on Linux the entries are recorded (path, uid, gid, mode, type) in an ownership record inside the workspace, then re-owned to the worker. The ordinary unprivileged archive, verify and guarded removal then run unchanged.
- Adoption is gated by the caller's owner re-check in a short transaction, never crosses a mount, refuses multi-link foreign files and non-regular record paths, finishes interrupted adoptions, and applies owner-access grants without following links.
- A retention pass after gc removes archives older than a configured number of days (default 7; 0 disables only the age rule), and under disk pressure removes the oldest archives past a minimum age first. It also removes sufficiently old partial builds whose creator PID is no longer alive. It never follows symlinks, skips live partial builds and archives carrying detach-intent files, and an unreadable policy removes nothing. Settings are read from the installation-root config; failures are logged and never abort gc.
- Archive age runs from the newest entry up to two levels below the archive. A bundle step no longer produces an empty bundle when a promisor-clone walk lists pushed history; the plain walk is cross-checked.

## How it works

kanban_scratch_archive_ownership.adopt runs at the start of archive_workspace; kanban_scratch_archive_retention is called from kanban_ops after the scratch pass; kanban_scratch_archive_shapes.select_unpushed intersects the stricter and plain walks.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/kanban_ops.py`
- `hermes_cli/kanban_scratch_archive.py`
- `hermes_cli/kanban_scratch_archive_ownership.py`
- `hermes_cli/kanban_scratch_archive_retention.py`
- `hermes_cli/kanban_scratch_archive_shapes.py`
- `hermes_cli/kanban_scratch_custody.py`
- `hermes_cli/kanban_worktree_gc.py`

## Tests

Entry-path behavior above is source-reviewed but **untested in this review**. The files below identify source regression targets, not proof that this review exercised those entrypoints.

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/hermes_cli/test_kanban_scratch_archive_ownership.py`
- `tests/hermes_cli/test_kanban_scratch_archive_partial_clone.py`
- `tests/hermes_cli/test_kanban_scratch_archive_retention.py`
- `tests/hermes_cli/test_kanban_scratch_archive_shapes.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Hard-linked trees stay kept (they would need a copy).
- The promisor workaround targets one older git behavior; the regression injects that walk's output because newer git does not reproduce it.
- The ownership adoption needs privilege escalation; without it entries remain refused as before.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes Kanban garbage collection and worker disk custody inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.

# Recognize a managed worker’s bound ancestor in leaked-env diagnosis

Patches: 1151.

## Behavior

- Worker-shell ancestry can be established by the run’s live bound managed-worker PID or delegation owner, not only by the dispatcher client spawn PID.
- The CLI fence remains in force; a valid worker shell no longer receives false leaked-environment recovery advice because its client PID is a sibling.

## How it works

`top_level_env` inspects a live ancestor matching the bound worker or `HERMES_DELEGATED_CHILD_OWNER`. This supplements the existing parent-agent identity checks instead of treating inherited worker variables alone as authorization.

## Code

- `hermes_cli/top_level_env.py`

## Tests

Source regression coverage:

- `tests/hermes_cli/test_leaked_worker_env.py`

See [mirror-1157-verification.md](mirror-1157-verification.md) for the exact files freshly executed for this publication and their results. Files listed here are source coverage references, not a claim that every file was rerun.

## Applying

Apply the complete numbered series at the frozen merge-base with `git am -3 --empty=keep`. Earlier patches include a curated integration overlay; individual entries are not promised to install independently. See [the inventory](README.md) and [verification report](mirror-1157-verification.md).

## Gotchas

A dead or non-ancestor PID is not proof of a live parent agent. No live Desktop quit or restart is needed or exercised by this mirror.

## Plugin?

No. This changes core lifecycle, admission, discovery, routing or its tests/workflows rather than a plugin-owned extension point.

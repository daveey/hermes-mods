# Public mirror: 456-patch series (0454–0456 appended)

Patches 0454–0456 continue the series after 0453. They are the three source commits after `8ae2a621a9` (0452):

| Patch | Source | Doc |
|---|---|---|
| 0454 | `a7d87690e0` docs: `kanban.sync.enabled` | [kanban-sync-seams.md](kanban-sync-seams.md) |
| 0455 | `d6dc1fe8d7` dispatcher honors the core-update drain hold | [core-update-drain-hold.md](core-update-drain-hold.md) |
| 0456 | `fb67f2fac2` never-run guard release still posts the queue-rejection note | [kanban-respawn-guard-hold.md](kanban-respawn-guard-hold.md) |

- Patches 0001–0453 are byte-unchanged.
- A fresh detached worktree at `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63` ran `git am -3 --empty=keep` on
  0001–0453 (453 applied), then `git cherry-pick -x` of the three sources applied cleanly; 0454–0456 are
  `git format-patch` of those picks, scrubbed like the rest.
- In that tree, `scripts/run_tests.sh tests/agent/test_core_update_drain.py tests/hermes_cli/test_kanban_pr_rejection.py`
  passed 16 of 16.
- The private denylist scan was clean for the new patches and docs; `python3 -B core/check_coverage.py` passes.

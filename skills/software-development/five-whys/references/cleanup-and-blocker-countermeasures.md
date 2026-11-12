# Cleanup and blocker countermeasures

Reference for skill `five-whys`: its SKILL.md links this file from the step that needs it.

## 6. Countermeasures in proportion

For false human-blocker reports, trace **producer → stored block metadata → UI classifier → action** before
rewriting the callout. Distinguish missing authorization from missing execution capability or authentication:
`sudo -n` requiring a password does not establish that approval is missing. Prepare the supported interactive
authentication flow when needed, rather than another vague approval card. Require a concrete user action and
why it needs that person; keep missing/unknown metadata visible as an agent-owned issue, not a default human ask.
Test both rejection of non-actionable capability/initial blocks and preservation of genuine approvals. Inspect
existing tests for assertions that enshrine the faulty default; passing them alone does not validate the fix.

For worker disk exhaustion, separate workspace retention from generated fixture-home payloads and orphan install environments. Inspect host-local scratch custody receipts before treating an evicted primary task row as lost ownership. A scratch safety scan may reject every symlink, while its archival fallback requires a Git repository; test a completed non-Git fixture home with interpreter symlinks before assuming a shorter retention window fixes reclamation. Preserve logs and evidence outside repositories: a Git-only archive does not preserve the whole workspace. Use the existing lease-aware install-state collector for rebuildable orphan environments, not directory-age deletion. Before deleting an archived source, flush not only payload files and archive directories but every containing ancestor entry that makes the archive reachable. Existing ancestors can be leftovers from an interrupted unflushed mkdir; existence is not durability. Test absent archive roots, new board directories, retry-existing chains and ancestor-fsync failures that must preserve the source.

For synced Kanban workspace cleanup, inspect sync-shadow replicas before calling a directory orphaned: gateway-local primary boards can evict remote-owned cards while their scratch directories remain. Protect any active copy across primary/profile/replica boards, use the latest finished activity, and abort retention on unreadable ownership sources. Test replica-only completion and active-copy veto; absence from the primary is not proof of abandonment.

For PID-owned temporary-home cleanup, exercise the real process scanner under the reaper's service UID as well as fixtures. Same-user non-dumpable processes can make an unprivileged `/proc` scan fail closed permanently; preserve that safety behavior and verify a privileged scheduled fallback instead of ignoring permission errors. Cover both configured scratch and the OS temp root, and test a killed creator whose child still holds a descendant file.

For cleanup incidents, distinguish a creator's lifecycle reaper from a generic age-based GC backstop. Trace
creator ownership from markers, task records or creation commands; directory names alone are not attribution.
Before exempting a creator from burst monitoring, verify its reaper is discovered and enabled in the actual
runtime and exercises cleanup safely on an isolated fixture. Source code existing on disk or a healthy generic
GC timer does not prove creator-owned teardown works. Scope exemptions to that verified creator, not a shared
scratch or code root that also contains unmanaged work; retain leak detection for exempted creators.
When a reaper kept a tree but logged no reason, load the reaper module by path, stub only its remove/archive
function to print, and replay its decision code against the live record (board row, marker, `git status`): the
silent early `return` shows up directly, where reading the log cannot show it. A reaper that fires once on an
event (completion) must have a later retry for every reason it can decline (open children, a live process, a
close path that skips the hook, a git checkout inside the tree), or each decline lasts until the age-based
backstop. Confirm that backstop runs: find its schedule on every host (launchd, cron `jobs.json`, crontab, systemd
timers) and check that it ages out what the reaper keeps declining. A GC command nothing runs, or one that keeps the
same trees, is no backstop.

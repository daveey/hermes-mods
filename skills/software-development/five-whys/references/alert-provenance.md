# Alert provenance and ownership

Reference for skill `five-whys`: its SKILL.md links this file from the step that needs it.

## 1. State the problem as an observed fact

**When the trigger is an alert, card or Inbox message, first prove that the production path raised it.** Test
fixtures, dry runs and other copies of a job can post the same alert with the same title. Do these checks before
you debug the reported failure:
- Match the alert's creation time against the real job log and journal
  (`journalctl --user -u <svc>`). Did a real run fail at that moment?
- Compare the alert's error text with what the code would really print. A path-less `cannot read board`, where
  production prints `cannot read {db}: …`, is a hard-coded fixture string. Grep the tests for it.
- Read the alert store directly for the post time; for the Hermes Inbox that is `~/.hermes/inbox/inbox.db`,
  table `items`, field `created_at`. Then check git history around that time for test work and fixes already
  committed.

If a test raised the alert, the problem becomes "a test reached a live side effect". Fix and prove it at that
level: snapshot the alert store's row count, run the suite, and confirm the count did not change. Then trigger
one real run (for example `systemctl --user start <svc>`) and confirm its success line, instead of waiting
for the next scheduled run.

For multi-gateway queue alerts, distinguish the originating chat's gateway from the card's execution owner before comparing saturation: a local session can wait on hub-only cards. Verify card presence in the owner's fresh snapshot and read that owner's dispatcher logs. In `kanban dispatcher [name]: spawned=N`, name is the board slug, not the profile; scope spawn evidence by board and snapshot profile. Capacity warnings use the separate host-wide `kanban dispatcher stuck: ... at_capacity(...)` format. Anchor at timestamp/level/logger/message so quoted tool output cannot manufacture liveness. A failed `kanban_show` on the origin does not mean the card disappeared. When recovering a claim hold, inspect its child update process and use the updater's existing cooldown with safe holder termination; killing an idle drainer alone can immediately recreate the hold and strand the repair workers.

For Inbox audits, separate strict quality counts (every untouched archive, test/status card, unregistered poster) from consecutive-archive admission thresholds: accepted cards can reset muting without erasing a quality failure. Use decision timestamps for newly archived evidence, report missing timestamps and replica/registry coverage limits, and never exempt publication drafts from an explicit archive metric. Test repair-owner dedup across active, done and archived states: core create idempotency excludes archived rows, and a returned done ID alone proves neither new evidence delivery nor active ownership.

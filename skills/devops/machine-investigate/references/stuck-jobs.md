# Stuck jobs (hung, not hot)

A scheduled job that stopped producing results uses almost no CPU, so load triage never shows it. Use this when a
job's results stop advancing.

## 1. Confirm it is stuck

- **Compare the job's own result record with what its launcher logs.** Read the state file, last-tested marker or
  newest output against the schedule. A launcher that forks a detached child can log "started" on every tick while
  each child exits at once on the job's lock, so a "started" line proves nothing.
- **Count systemd's left-over warnings:** `journalctl [--user] -u <unit> --since <t> | grep -c 'Found left-over process'`.
  Logged when a oneshot unit starts, it means a child of an earlier run is still alive in the unit's cgroup, often
  holding the job's lock. Read the named PIDs with `ps -o pid,ppid,lstart,etime,stat,args -p <pids>`;
  `fuser <lockfile>` names the lock holder.
- **Check for progress:** read utime+stime from `/proc/<pid>/stat` twice, a few seconds apart. A hung process gains
  next to nothing.

## 2. Save the evidence before touching it

1. Create the evidence directory, then use `sudo -n py-spy dump --nonblocking --pid <pid> > ~/.hermes/kanban/evidence/<topic>/pyspy-<pid>.txt`
   (Yama may refuse a non-descendant without root). Frames can help identify a deadlock; corroborate them with
   repeated samples and the lock implementation before concluding it cannot progress. Keep
   evidence out of scratch dirs, which are pruned.
2. Find the owner as in SKILL.md step 4. If an open card owns the job, post the evidence there instead of stopping it.

## 3. Stop it and prove recovery

A confirmed deadlock may require stopping the run, but partial writes and in-flight work can still be lost.
Before stopping anything, verify ownership, retry safety and checkpoint/transaction state. Do not signal a live
Hermes gateway or backend; the SKILL.md backend protection applies here too. If stopping the supervisor could
kill unrelated work or falsely mark the run complete, use its supported cancellation path instead.

1. Stop the job's supervising process (the one that started the child and records its result) first, then the hung
   child, so the supervisor can't record the kill as a failed run or move its result marker.
2. Read back that the lock is free (`fuser <lockfile>` names no PID) and the result record is unchanged.
3. At the next scheduled tick (`systemctl [--user] list-timers`), confirm a fresh run holds the lock, gains CPU time,
   and has no thread in the deadlocked frames (`sudo -n py-spy dump`).
4. File two fixes: the code bug, with the saved dump, and the job's missing guard (a per-run timeout, a launcher
   that logs "started" when nothing ran, an alert when the result record falls behind).

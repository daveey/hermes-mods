# Five whys: PR stuck "approved" and never merged

1. Why undelivered? The PR stayed open while the agent believed only approval was missing.
2. Why open? After a rebase onto a moved base (conflicts repaired) the approval was dismissed, so the
   review gate was REVIEW_REQUIRED again, and one required check was still pending.
3. Why not noticed? The ad hoc watch script tested only OPEN/MERGED/CLOSED, with no mergeability, check
   or review classification (evidence: task handoff).
4. Why repeated? The watcher died on restarts and was restarted blindly, each time escalating a generic
   BLOCKED to the user without proof (evidence: handoff). The earliest BLOCKED cause is unknown.
5. Why possible? No reusable one-shot classifier or durable retry existed (inference). Fix: the
   `pr_blocker_check.py` script, regression tests, and the scheduled-retry rule in SKILL.md.

Also observed: an `--admin` merge authorized by the user was still rejected by repository rules requiring
an approving review from someone with write access; the blocker was real, not guessable from "BLOCKED".

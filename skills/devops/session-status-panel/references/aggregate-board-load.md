# Aggregate BoardLane load verification

Distinguish per-session cooldown from aggregate producer duty: capacity1 plus completion-based cooldown can remain continuously busy when many independently dirty watched sessions fill the slot. Verify both same-session completion gaps and global read intervals before claiming a total-load target.

On Linux, `session-panel-c` is a truncated name shared by the service and short-lived card workers; endpoint-survivor `/proc` deltas undercount churn. A read-only `sched:sched_switch` trace filtered by serving TGID and comm captures every matching thread lifetime. Sum per-TID counts over the measured trace interval, retain raw output and confirm active PID/birth/health before and after. Natural-load before/after windows are not a controlled causal A/B. A successfully loaded mitigation with a failed numeric gate remains incomplete.

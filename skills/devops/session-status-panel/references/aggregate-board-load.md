# Aggregate BoardLane load verification

Distinguish per-session cooldown from aggregate producer duty: capacity1 plus completion-based cooldown can remain continuously busy when many independently dirty watched sessions fill the slot. Verify both same-session completion gaps and global read intervals before claiming a total-load target.

On Linux, `session-panel-c` is a truncated name shared by the service and short-lived card workers; endpoint-survivor `/proc` deltas undercount churn. A read-only `sched:sched_switch` trace filtered by serving TGID and comm captures every matching thread lifetime. Sum per-TID counts over the measured trace interval, retain raw output and confirm active PID/birth/health before and after. Natural-load before/after windows are not a controlled causal A/B. A successfully loaded mitigation with a failed numeric gate remains incomplete.

For churn-complete fact capture accounting, a bounded `rawtracepoint:sched_switch` observer can read `arg1`/`arg2` as previous/next `struct task_struct *`, filter each task's `tgid` to the exact backend PID, and sum switch-out minus switch-in nanoseconds keyed by comm/TID. Preflight the installed bpftrace first: BTF may expose `task_struct.comm` as `string[16]`, in which case use `$task->comm` directly rather than `str($task->comm)`. Include shared `fact-change-fee` capture work, not just `fact-board-cap`; retain trace errors and boundary omissions, and fail CPU acceptance closed when the trace is unavailable. Arm an autonomous abort observer before an instance-only feature override, persist its script/output outside disposable scratch, and remove only the exact owned override on failure without signalling the serving backend.

Do not equate sidebar interest with a live panel subscriber: `/changes` can be an
immediate cached read registering the whole sidebar. For visible-only admission,
use an explicit mounted-panel lease, release on unmount/focus switch/window hide,
and expire abandoned clients. Preserve hidden dirty demand until an attach queues
its refresh; an older in-flight read must not clear freshness suppression while
that hidden debt remains. Test the detach → hidden delta → old completion
interleave with a newly forced heartbeat, not an old emitted event. Metadata-only
lease calls must not consume cold-model response scripts or retry counts in React
fixtures. Verify live activation via policy/subscriber/dirty status counters before
interpreting a low scheduler count as successful load reduction.

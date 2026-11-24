# Memo-hit latency acceptance

Treat natural `unchanged=true` memo hits and latency as separate gates: a hit can still spend seconds in membership capture, local reconciliation or contention. Retain all raw cohorts and per-chat sample counts; if observed unchanged calls exceed the target, report FAIL and profile the successful hit path before another observation window. Do not infer that a corrected source contract has met the end-to-end latency target.

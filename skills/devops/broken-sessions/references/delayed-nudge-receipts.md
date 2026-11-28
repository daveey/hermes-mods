# Delayed nudge admission receipts

Treat detached owner admission latency separately from scanner errors. Compare the frozen full text with the exact target's active user row, then let the next natural tick recover the receipt. Never manually resend an unknown delivery or clear its write-ahead identity. A reply proves owner activity, not original-goal completion; routing failures can occur after successful admission.

Keep pending deliveries visible in `pending_delivery`, separate from `errors` and `error_count`. Read both fields when qualifying rollout: zero scanner errors alone does not prove delivery. Report each rule's actual live/shadow mode rather than the global nudge mode when per-rule overrides apply. Preserve historical false-error tick rows as pre-fix evidence; do not rewrite the journal.

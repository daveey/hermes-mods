# Owner-scoped reconciliation captures

Capture complete membership inputs rather than filtering only previously known IDs: replica arrivals/removals, ledger-only obligations/receipts and canonical compression aliases all invalidate. Include alias/re-home creation identity; reused IDs in other chats must not churn the key.

Keep source-mode selection explicit: the legacy replica/ledger path does not cover `links_reader=replica` chat-link shadows, so an unsupported mode must return unknown. Compare unknown as changed even for repeated unknown reads; raw DB/WAL identity and a relaxed global fingerprint are not semantic proof.

Compose ActionStore request/binding/operation and historical namespace dependencies before wiring a capture into a memo, and retain single-flight, deferred backoff and a non-sliding canonical ceiling. A standalone capture is not itself live latency proof.

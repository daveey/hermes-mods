# Independent source admission and fair service

An independent task-refresh lane must admit external hub/origin-ledger/replica writes as well as local board revisions: a file-watch wake alone proves nothing if unchanged board stamps skip capture. Exercise an external-only semantic change while the full sweep is blocked, and real adapter observation-only pulls without hydration or renewed execution clocks.

Bound service priority separately from payload coalescing: preserve the first unserved admission time and use oldest-first/bounded-aging scheduling. A continually rewritten newest-first queue can starve a quiet changed chat despite bounded memory. Test saturated capacity with continuing newer arrivals.

Test sync-bookkeeping invalidation with real replica SQL and canonical projection adapters. A `pulled_at`-only commit must carry `synced_at` without renewing `computed_at`/execution evidence or hydrating every chat. Admit board-shadow writes even without a hub/ledger write. Canonical hub rows may lack `source='replica'`, and selection may add `source='origin-obligation'`: match observation producer plus owner/profile/board/card/session, not a raw source label or bare card ID. Keep true stale/status/receipt changes semantic, allow legitimate clock regression on source reset, and exercise a real concurrent producer publishing an older capture after a newer observation.

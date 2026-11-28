---
name: latency-watch
description: "Use when something is slow. Offer latency_watch."
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [latency, performance, measurement]
---

# Add a slow operation from chat

When the user says something is slow, offer `latency_watch` to put it in the
Latency panel with an explicit p90 target. Do not substitute an unmeasured
performance claim or create instrumentation work before the user adds it.

1. Name exact start and end events. Call `latency_watch(describe="start -> end")`,
   or supply `definition={id,label,group,kind,source,start,end,unit,target_p90}`.
   Targets are positive **milliseconds**, unit `ms`; groups are `Using Hermes`,
   `Cards & workers`, or `Ship`. The default 60s target is a proposal, not an SLO
   the user has already chosen. Use a full definition for a more appropriate target.
2. Emit the returned `::latency-watch{definition="..."}` directive on its own line,
   exactly as returned. The Latency Desktop plugin renders the definition's
   start, end, target, history and **Add / Change target** controls. Do not claim
   it has been added merely because the tool returned a proposal.
3. Add runs only on the user's click, against the Ledger's hub. It atomically
   appends to that collecting profile's `latency/metrics.local.yaml`. Repeated
   clicks/retries do not duplicate a definition or its instrumentation card.
4. Known derived board pairs (`decision_requested -> unblocked`, `created -> claimed`,
   `created -> completed`) backfill 30 days on the next collector cycle. Full
   state definitions support `typed_user -> first_assistant|explicit_final`, or
   `tool_name -> tool_result`. Unlogged event pairs require `kind=span, source=spans`.
5. A new span files one qualified, linked instrumentation card through normal
   Kanban creation. The panel shows **collecting from today** (then its Add date)
   while no measurements exist, never a fabricated zero or synthetic history.
   The link is visible in the proposal result and expanded panel row.

For instrumentation verification, read `references/collector-verification.md`.

If Desktop is unavailable, show the definition in text and explain that Add needs
the enabled Latency Desktop plugin. Do not quietly write a different machine's
registry or bypass dashboard authentication. Fix an Add failure and retry the same
metric identity; never invent a card id. Existing ids with different definitions
or targets are refused rather than silently replacing them.

# Selected B: approved visual monitor revision

This is a sanitized implementation specification of the approved revision, not a new design direction. Original design-plan SHA256: `be12ed903831ceafba42e7bdb9d45ed834e6f71cc33b72cebc2da649af237777`; illustrative HTML SHA256: `ccd43a35a6ef18e4cec2ab1ac37085e9d8aa2212cd8d3649aa6969658a707867`. Original machine paths, personal inventory and coordinator identifiers are intentionally not published. The source design's illustrative observations are not production telemetry.

## Composition

Keep the native status trigger and upward/end-aligned Monitor popover, width min(680px, viewport minus 24px), maximum height 70vh. Header, scope, Refresh, search/state selector, diagnostics and shortcut hints remain accessible; only inventory scrolls. All physical nodes initially collapse. Expand node → registration → inline evidence and actions. No permanent empty inspector and no replacement app chrome.

Node identity/count → 8px segmented registration bar → icon/count labels → concise blocking reason/age. Move OS/architecture, CPU/memory absence, provenance and capacity to expansion. Unassigned is a prominent full-width outlined cohort with its own bar and 'No physical node mapping'. Never infer host identity from registration names.

Blue ▶ means running; neutral outlined ○ means idle; amber diagonal hatch Ⅱ means held/degraded; red × means failed; gray dashed pattern ? means unknown. Always expose text and numeric counts, never color alone. Bar denominator is the visible deduplicated registration cohort, not host utilization or capacity. Segment minimum widths aid legibility; numbers are authoritative. Zero mapped registrations is distinct from zero idle or zero load.

## Evidence model and precedence

Retain original provider registration/activity independently from the display bucket. Use injected time for deterministic tests. Source envelope must be fresh, observed no more than 120 seconds ago, not future-dated and not expired. Require matching per-registration provenance and valid timestamps, runner freshness and complete coverage. Disconnection, owner conflict, incomplete coverage or stale required evidence prevents current eligibility/actionability.

Hold/check/provider/job/host observations retain independent timestamps and expiry. A heartbeat never refreshes their ages. Expired holds do not establish resumed listeners or idle activity. Preserve last-known activity and fresh independent hold evidence alongside stale activity. Mixed source ages are labeled Mixed ages; never stamp every child with the newest envelope.

First matching bucket rule:

1. Invalid/conflicting identity or insufficient/stale required evidence: unknown. Offline plus busy is conflicting, not idle.
2. Fresh independent required-check failure: failed; simultaneous hold remains visible. Expected offline under a valid hold alone is not an independent failure. Desktop never invents consecutive offline observations.
3. Fresh positive online/busy: running. A simultaneous hold says draining/no new jobs, not stopped.
4. Valid explicit hold without busy: held, with source and reason. Fresh warning uses the amber style but says degraded, not operator-held.
5. Offline/missing/unknown registration, unknown activity or unresolved expired hold: unknown, with the specific reason.
6. Fresh online/explicit idle without hold, warning, failure or conflict: idle. This is provider activity, not readiness or a free slot. Unknown checks remain not reported; independently fresh passed checks alone support the Ready filter.
7. Everything else: unknown, not numeric zero.

Consume the existing owner's optional `operational_evidence`; no new detector, SSH collector, name heuristic, privilege or mutation. A host maintenance overlay without affected-registration evidence remains host-only. Historical supervisor events are historical, not current holds. Unavailable sources stay Not observed.

Contract integration: the owner's optional `readiness_evidence` supplies per-check causes. Only failures the owner reports as hold-explained and whose matching hold is current on the client are subtracted (service only under a current systemd drain); any remaining failure is independent and red. Missing, stale, expired, malformed or inconsistent causes keep the conservative aggregate failure. Failures are never suppressed merely because a hold exists. Unobserved required keys show as Not observed; warnings remain visible.

## Aggregation and native behavior

Use owner gateway UUID/profile + host.id, enumerate zero-registration hosts, retain existing provider/endpoint/scope/provider-runner deduplication. Unknown is a segment alongside known subsets. Filtered header says total registrations and shown count; bars/counts use shown rows only. Node reason priority is independent failure, hold/degradation, then unknown monitoring. Runner failures never imply node offline. Reachable is management reachability, not host health. No CPU/memory meter exists when the contract lacks samples; expansion says not reported. Capacity requires enforced=true, valid limit and independently observed active jobs; never subtract registrations to invent slots.

Native filter labels: All, Needs attention, Running (Busy), Idle, Held/maintenance, Failed, Unknown/stale, Checks passed + idle (Ready). Intentional healthy maintenance alone is not an attention failure. Preserve identity-keyed expansions and details on polling, new nodes collapsed, pruning against complete inventory not filtered results.

Use inherited foreground/secondary/surface/stroke/hover/accent tokens and system font; accessible native success/warning/error tokens with tested fallbacks. Target text contrast 4.5:1 and indicators/focus 3:1. Validate dark/light and custom themes. No web font, decorative dashboard cards or gradient hero.

Preserve actual caret, selection, composition and focus. Unmodified typing outside editable controls enters search. Arrows navigate visible rows; Enter/Space toggle; Right/Left expand/collapse; Escape unwinds menu/help, detail, query, node, then popover and restores focus. Preserve native palette node/runner/settings aliases, context menu/right-click/Shift+F10, question-mark help and scope controls. Do not intercept modifier or composing input.

Keep existing full owner routes, route capture/revalidation, epoch fences, query keys, deduplication, lazy detail and refresh behavior. Repair/maintenance controls remain capability-gated and unavailable when unsupported. Current job URLs retain owner/runner/scope/repository/ID/provenance/freshness guards. Empty jobs never prove idle. Distinguish unattributed, not configured, forbidden and unavailable job monitoring.

## Acceptance evidence

`tests/visual-v2.test.mjs` and `tests/readiness-evidence.test.mjs` cover state precedence, backend-faithful hold-only/independent/maintenance-service/not-observed cause shapes, malformed-cause fallbacks, timestamp/coverage/identity gates, duplicate aggregation, zero hosts and lazy job guards. Browser harness renders the actual plugin with React/Radix and contract-faithful fixtures, including 57 registrations and 45 unassigned. Its README documents commands and limitations. Saved screenshot `screenshots/visual-v2-dark-390.png` is a sanitized actual-plugin harness capture with fictional names and contract-faithful readiness causes; it is not packaged-app or live-data proof.

Required release gates remain: all existing/new tests, owner relevant contract tests, real browser input/layout at native/900,640,390 widths, dark/light and short heights; custom-theme contrast; screen-reader walkthrough; pinned independent review with served-model verification; exact-head CI/merge; normal sync without restarting active turns; packaged-app screenshot and authenticated live-field readback. Harness success alone does not satisfy these release gates.

# Mirror reconciliation: public series vs private main (2026-10-27)

Range `upstream/main..origin/main` of the private fork, no merges: **532 commits**. Public series before: 477 files (numbers 0001-0476, 0468 used twice; 0453 and six rebased orphans kept or dropped as noted). After: **525 patches, 0001-0525**, in commit order.

Method: a commit counts as present when its SHA header or its `git patch-id --stable` matches an existing patch (470 commits, 418 by SHA and 52 by patch-id). The other 62 were compared by added-line overlap against the whole existing series, then classified.

| Class | Count |
|---|---|
| present by SHA or patch-id | 470 |
| already-equivalent (rebased/squashed/duplicate; patch kept or folded) | 13 |
| partially present (rebased orphan keeps most lines) | 1 |
| genuinely missing, new patch added | 48 |

Orphans (old patches whose source commit was rebased away) kept in place because they replay cleanly in series order: 0251, 0393, 0403 (old 0404), 0430 (old 0431), 0451 (old 0446), 0453 (old 0448). Old 0453 (PR-continuation fixtures) was dropped: its 32 added lines are present in other patches of the series.

## Per-commit table (the 62 commits not matched by SHA or patch-id)

| Source SHA | Subject | Class | New patch |
|---|---|---|---|
| `c6a560b2e1` | kanban guard-hold notice: file a fix card for default instead of posting to the  | already-equivalent (rebased orphan patch (content 88% by added lines, remainder is a doc-comment rewording)) | 0251 |
| `18c419d2a9` | fix(kanban): require actionable evidence for human escalation [daveey] | already-equivalent (squash patch of the integrated merge) | 0393 |
| `53a6693092` | test(kanban): migrate timed-park sidebar fixture to evidenced requests [daveey] | already-equivalent (folded in the same squash patch) | earlier patch |
| `6ab333af32` | fix(kanban): require actionable evidence for human escalation [daveey] | already-equivalent (duplicate of 342 (same diff), folded in the same squash patch) | earlier patch |
| `d7235c1661` | test(kanban): migrate timed-park sidebar fixture to evidenced requests [daveey] | already-equivalent (folded in the same squash patch) | earlier patch |
| `dd13c9b77c` | test(kanban): preserve cache invalidation coverage for evidenced requests [davee | already-equivalent (folded in the same squash patch) | earlier patch |
| `c13f6de33e` | test(kanban): supply explicit evidence in integrated routing fixtures [daveey] | already-equivalent (folded in the same squash patch) | earlier patch |
| `3d0d4ea6a4` | test(kanban): follow qualified replica reader for actionability [daveey] | already-equivalent (folded in the same squash patch) | earlier patch |
| `b1716989ce` | test(kanban): migrate actionable receipt and waiting fixtures [daveey] | partially present (rebased orphan patch carries 16/21 added lines) | 0430 |
| `2f45b7b686` | fix(desktop): keep new Home sessions visible across sidebar views [daveey] | already-equivalent (rebased orphan patch, 452/452 added lines) | 0403 |
| `a0c15d5af9` | [daveey] Add durable exact-owner prompt admission with safe cancellation | genuinely missing (new patch) | 0441 |
| `957b76cb38` | [daveey] Add durable exact-owner prompt admission with safe cancellation | genuinely missing (new patch) | 0442 |
| `991588aed4` | [daveey] Project committed decision user rows to the owning Desktop session | genuinely missing (new patch) | 0443 |
| `5012b9868d` | [daveey] Narrow admitted event payload to generated wire contract | genuinely missing (new patch) | 0444 |
| `1f220668a6` | [daveey] Fix admission event code-health gate without changing guards | genuinely missing (new patch) | 0446 |
| `fa073eb288` | fix(kanban): wake probes that can never succeed no longer look like 'not yet'; a | already-equivalent (rebased orphan patch, 176/178 added lines) | 0451 |
| `f75d239f8b` | fix(kanban): respawn guard active_pr ignores cards whose worker never ran | already-equivalent (rebased orphan patch, 46/46 added lines) | 0453 |
| `4695da764b` | fix(kanban): owner scope heartbeat window 450s so remote release stops flapping  | genuinely missing (new patch) | 0461 |
| `e90c1654a4` | fix(kanban): do not resurrect a remembered legacy remote row whose qualified twi | genuinely missing (new patch) | 0462 |
| `bbb6290077` | fix(lint): Windows footguns in kanban_provider_walls and core_update_drain (#107 | genuinely missing (new patch) | 0465 |
| `6053646f27` | feat(kanban): central sync client — shadow mode, import, diff (phase 2) [daveey] | genuinely missing (new patch) | 0466 |
| `954bfad790` | fix(desktop): model pill + effort menu use the chat's stored model while its age | genuinely missing (new patch) | 0467 |
| `726d5b5525` | fix(kanban-sync): send the full row for an edit of a card the service never saw; | genuinely missing (new patch) | 0468 |
| `9dc26ab68c` | fix(kanban-sync): read kanban.sync with the repo's YAML loader, not PyYAML [dave | genuinely missing (new patch) | 0470 |
| `a7caf2ddb7` | fix(kanban-sync): build the lease client from the hub enrollment in any process  | genuinely missing (new patch) | 0472 |
| `3c3effe767` | fix(tui_gateway): an accepted submit survives a client socket drop; unanswered m | genuinely missing (new patch) | 0473 |
| `41a47a3c32` | fix(kanban-sync): diff counts acknowledged-but-unpulled rows as in flight; refre | genuinely missing (new patch) | 0475 |
| `3232b40b24` | fix(desktop): name unknown models and preserve lazy resume route [daveey] | genuinely missing (new patch) | 0478 |
| `f6a16700fd` | fix(profiles): isolate scratch homes nested under the native root [daveey] | genuinely missing (new patch) | 0479 |
| `1164b8813f` | fix(providers): preserve fallback failures and bound quota holds [daveey] | genuinely missing (new patch) | 0480 |
| `582fab6bfb` | refactor(providers): keep fallback diagnostics within health caps [daveey] | genuinely missing (new patch) | 0481 |
| `d439b11518` | test(tui_gateway): use dict instead of lambda: {} (ruff PIE807) (#115) | already-equivalent (one-line ruff fix already present in an earlier patch) | earlier patch |
| `5803147af6` | fix(desktop): refresh kanban evidence for retained older sessions [daveey] | genuinely missing (new patch) | 0483 |
| `ffeb8f66fc` | fix(desktop): preserve task evidence across legacy reads and activation races [d | genuinely missing (new patch) | 0484 |
| `589534bac5` | fix(desktop): publish kanban freshness-only row changes [daveey] | genuinely missing (new patch) | 0485 |
| `47721dc645` | fix(tests): decouple canonical room reuse from lease timing (#91) | genuinely missing (new patch) | 0489 |
| `236b07480e` | fix(kanban): attest inherited legacy task aliases with exact graph proofs [davee | genuinely missing (new patch) | 0490 |
| `6b6a16c01a` | test(delegation): tolerate scheduler starvation in cancellation handshakes [dave | genuinely missing (new patch) | 0491 |
| `fc979f0c25` | fix(copilot): drain final ACP response before reporting exit [daveey] (#99) | genuinely missing (new patch) | 0492 |
| `e73677e9fc` | fix(agent): withhold rejected final output until final transforms (F3 core compa | genuinely missing (new patch) | 0493 |
| `c65a9c98b5` | fix(tests): await connector RPC pipe delivery on throttled CI [daveey] (#90) | genuinely missing (new patch) | 0494 |
| `4f7a8404c5` | test: synchronize shared compression lock attempts and await workers (#100) | genuinely missing (new patch) | 0495 |
| `dc5c245525` | feat(update): cross-process admission fence [daveey] (#98) | genuinely missing (new patch) | 0496 |
| `d121f4c697` | fix(kanban): scope remote observations to linked cards | genuinely missing (new patch) | 0497 |
| `b23a0d382e` | test(kanban): linked remote card is observed without descendants | genuinely missing (new patch) | 0498 |
| `b032e92976` | test(desktop): raise timeouts for CI-load-sensitive plugin probe and hub poller  | genuinely missing (new patch) | 0499 |
| `12758cf08f` | test(kanban): legacy path test follows linked-card-only observation scope | genuinely missing (new patch) | 0500 |
| `78130169e3` | perf(kanban): linked cards never wait on the remote-links rebuild; rows carry ac | genuinely missing (new patch) | 0501 |
| `0fabd3eb47` | fix(kanban-sync): close the capture gap when a process (re)installs triggers [da | genuinely missing (new patch) | 0502 |
| `07a0dd02b8` | feat(kanban-sync): Board push channel and event-driven rounds [daveey] | genuinely missing (new patch) | 0503 |
| `d83286a1bd` | fix(model_routing): compare token estimate, not UTF-8 bytes, to route context ca | genuinely missing (new patch) | 0504 |
| `ca4285335f` | fix(kanban): stale remote-links reads only for the linked-card strip [daveey] | genuinely missing (new patch) | 0505 |
| `da1a3a8443` | feat(kanban): chat links synced through the Board (RFC phase 5) [daveey] | genuinely missing (new patch) | 0506 |
| `20ca50ce3f` | fix(kanban-sync): retry transient rejections instead of stranding rows [daveey] | genuinely missing (new patch) | 0507 |
| `e1db6f8e77` | fix(kanban-sync): back off transient rejections by time; send child rows whole [ | genuinely missing (new patch) | 0508 |
| `193659fa7e` | fix(kanban-sync): send a child row's unacknowledged card first [daveey] | genuinely missing (new patch) | 0509 |
| `7c5b978a33` | feat(kanban): unclaimed notices start a turn, not just a transcript row; log pol | already-equivalent (same SHA as the existing patch (shadowed by a duplicated number 0468 in the old series)) | 0518 |
| `1fe662a3a4` | fix(shared-metrics): never offer; an unanswered profile reads as "No thanks" | genuinely missing (new patch) | 0519 |
| `d56b621070` | fix(model-routing): aux class requirements use token units; title JSON best-effo | genuinely missing (new patch) | 0520 |
| `42b8d9b755` | fix(desktop): distinguish exhausted providers from first-run setup [daveey] | genuinely missing (new patch) | 0521 |
| `aa86510788` | test: fix red main CI (ruff PIE807, desktop @/hermes mocks missing getApiRequest | genuinely missing (new patch) | 0524 |
| `98906dadc7` | fix(desktop): refresh preview-only kanban badges on focus and backstop [daveey] | genuinely missing (new patch) | 0525 |

## Checks

- Privacy: new patches pass `core/` denylist scan (`.githooks/pre-commit`), `From:` identities rewritten, one live ssh target rewritten to `hub.example.com`, co-author trailers normalized.
- Sequential `git am -3` replay from the merge-base `4957bbd0a0` cannot cover the whole series: it passes 0509 and stops at 0510 (and the pre-existing series stopped at its 0467, the same patch) because merge-commit conflict resolutions are not patches; real commit cherry-picks fail the same way. Per-commit equivalence above is the authoritative check.


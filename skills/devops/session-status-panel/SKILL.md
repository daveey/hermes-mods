---
name: session-status-panel
description: "Use when the status panel shows wrong or missing fields."
triggers:
  asks:
    - "the status panel shows X wrong"
    - "add a field to the session panel"
    - "how does an unblock card render"
    - "blocked section shows cards but no session is red"
  not_for: "writing a human ask (use generate-unblock-card); deciding whether a human is needed (use try-to-unblock)"
version: 0.1.0
author: David Bloom (daveey), Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [desktop, kanban, status-panel, panel-model, unblock]
    related_skills: [generate-unblock-card]
---

# Session status panel

Compute presentation on the backend; render that snapshot on Desktop. The model is not permission to execute a choice. All paths below are relative to the hermes-mods repository; work in an isolated worktree, not its deployment checkout.

## When to Use

- The status panel shows a wrong section, count, title, option or recommendation.
- Add a field to the session panel, or understand how an unblock card renders.
- Verify the same local, remote-gateway and triage ask layout.
- For writing the ask itself, load `generate-unblock-card`; this skill owns the rendering contract.

## Prerequisites

Read [PanelModel v1](../../../plugins/session-panel/MODEL.md), the machine-readable schema and ownership notes. Inspect the current producer and renderer before changing a field: that document includes historical phase notes, not promises that every gateway has the same deployed version. Use `read_file` and `search_files` for source inspection; use `terminal` for the test commands below, from the worktree root.

## Contract and input map

`dashboard/feed.py` publishes `bundle.model` through `POST /api/plugins/session-panel/changes` and `plugin.session-panel.changed`. Desktop caches by connection/profile/session, not session ID alone.

| Output | Authoritative input / transformation |
|---|---|
| `version`, `revision` | `model.py:reduce_panel(bundle, now=...)`; version 1, deterministic SHA-256 of canonical model JSON excluding revision. Not the feed's monotonic `rev`. |
| `header.state` | `inputs.py` reads canonical session `outcome_json.state` or `.kind`; absent/unreadable means `unknown`. Never derive header state from row counts. |
| `sections` | Six arrays: `needs_you`, `stuck`, `running`, `waiting`, `ready`, `done`. Feed `tasks` plus hydrated `cards`, current requests, execution annotations, rentals and confirmed done history feed the pure reducer. |
| `header.counts`, `header.pips` | Unique displayed rows, per section; only nonempty sections produce pips, in section order. |
| Row identity | `id`, owner-qualified `key`, `kind`, `gateway`, `profile`, `board`; retain the exact source coordinates for deduplication and opening. |
| Row text | Backend-authored `title`, `detail`, `details[]` from card and execution evidence. Suppress parent text only when that same dependency is already visible in the rendered hierarchy; retain flat/hidden and additional parents. Never suppress the row's own status. |
| Ask content | `source`, `situation`, `options[]`, `only_you`, `recommend_letter`, `reason` from `human_request` via `brief.py:parse_brief`, or current question-store content. |
| Answer binding | Current request `request_id`, `request_revision`; each option's `choice_id`; `decide` action carries the exact request owner. No guessed IDs or index-based binding. |
| Other actions | `open_card.target={id,gateway,profile,board}`. Action labels are display data, not commands. |
| `diagnostics` | `input_errors` distinguish unavailable sources from a known empty source. `computed_at`/explicit `now` fix the read clock. |

`inputs.py` reads current lifecycle/resolution from the action store, not the frozen revision's old state. It invokes user-steps mirror reconciliation through the reader and starts the shared rental refresh. The reducer itself performs no I/O, migrations, reconciliation, decisions or implicit clock reads.

Classification details live in MODEL.md and `model.py`, not a second implementation here. In particular: preserved `block_kind` alone is not a live human wait; stale remote reports do not prove completion or running; unresolved execution evidence is explicit unknown Waiting; retired requests never reopen a decision. Subagents/processes require canonical execution evidence. Rentals are owner-filtered rows. Confirmed done history is owner-qualified and deduplicated.

## How a brief becomes tiles

The shared parser is [brief.py](../../../plugins/session-panel/brief.py). Ask-gate's `unblock_card_quality.py` uses its raw `sections()` and `options()` grammar; `parse_brief()` adds presentation. Acceptance and display share parsing, but display does not replace quality validation.

1. Write `Situation:`, `Options:` and `Recommend:` on separate lines in `human_request.scope`. Use `A) Short name: consequence; cost; reversibility` per option. Prefer this canonical form even though the parser tolerates headings, bullets and some inline labels. Machine-contract lines (`DEVICE:`, `HUMAN_STEP:`, `OBSERVATION:`, `EVIDENCE:`) terminate a parsed part; preserve contract scopes exactly.
2. `choices[].label` controls tile order **and the displayed name** when choices exist. Use `C: Test live (recommended)`, not an opaque label. Unique own letters are preserved, including C/A ordering; missing/duplicate letters or insufficient room for Skip/Other use positional badges. Scope details join by letter, never list index. Without choices, the scope option's short name supplies the tile name.
3. A scope recommendation wins; keep `Recommend: C - reason` consistent with the single recommended choice. Only if no recommendation part exists does a unique recommended choice supply the letter. Keep its reason in the first paragraph: appended evidence/footer must not become recommendation copy.
4. `why_human = Tried: ... Only you can: ...` yields a visible `only_you` line and a folded tried trail. Put decision-critical consequences in the brief, not only in `action` or evidence.
5. `model.py:ask` adds request/choice bindings and consent details (plan, limits including permission flags, exclusions, outcomes, choice explanations). `desktop-plugins/session-panel/plugin.js` draws the model; session-tasks chips consume the same model. User-steps supplies decision submission, composer integration and pending receipt recovery, not a second set of card tiles.

A bound choice goes through the existing user-steps decision operation, with exact owner, revision and idempotency key. Unmirrored remote asks can retain a composer-draft fallback during a mixed-version rollout: a draft is **not** a recorded choice. Inspect the installed action and binding; never claim the remote answer path works from matching pixels alone. Never fabricate a binding, execute scope text, or replace the fenced decision operation with comment-plus-unblock.

## Add a section or row field

1. Trace the exact focused connection/profile/session to the feed input, then to `reduce_panel`. Reproduce with a pinned input before editing; a model read for a different chat is not a visibility diagnosis.
2. Change the backend input adapter and pure reducer first. Extend MODEL.md's schema, including counts/pips and section ordering for a new section. Decide missing/unknown semantics explicitly. Preserve canonical header/execution ownership, source coordinates, consent boundaries and current request lifecycle.
3. Add a Python regression for the field and negative cases (missing/stale source, duplicate owner IDs, retired request). Assert deterministic revision equality for identical input and a changed revision for changed presentation.
4. Update Desktop's model validation/section list and rendering only after the producer emits the field. Keep presentation fallbacks explicit for older models; do not reintroduce Desktop classification or brief parsing. Update session-tasks' model projection if the chip needs the field.
5. Extend the producer-to-DOM fixture to assert actual text, order, details and exact choice binding. Keep pending Check status/Retry recovery mounted even after a request leaves the visible decision queue. Passing counts alone do not establish preserved behavior.

Completion criterion: one authoritative producer, a documented contract, Python and actual-renderer regression coverage, and no duplicate parser/classifier.

## Golden-fixture recipe

Public structural cases live in `plugins/session-panel/tests/test_model.py`; `desktop-plugins/session-panel/test/hub-model-fixtures.py` serializes the **real reducer** into `hub-model.test.mjs`. Extend both sides, not a hand-built JavaScript imitation of PanelModel.

Through `terminal`, run:

```sh
python3 scripts/test-all --no-core --exact -k plugins/session-panel -k plugins/ask-gate -k plugins/user-steps
python3 scripts/test-all --no-core --exact -k repo-guards
node --experimental-strip-types --test desktop-plugins/session-panel/test/hub-model.test.mjs
```

Before the Node command, provision dependencies exactly as `.github/workflows/desktop-plugin-tests.yml` specifies. For full core-backed coverage set `HERMES_CORE_SOURCE` to an isolated compatible core checkout, `HERMES_DESKTOP_DIR`/`HERMES_DESKTOP_PACKAGE` to its installed test dependencies, and make nanostores resolvable from that core. Run serially and require zero core-dependent skips before claiming full coverage. No backend `plugins/session-tasks` suite exists; its tests are Desktop tests. Run all `desktop-plugins/*/test/*.mjs` when changing shared rendering/answer integration.

For a private historical golden, retain raw input, observation time, source revision and expected output in private task attachments. Pin `now` and source clocks to the recorded snapshot; distinguish historical replay from current production. Never publish personal/employer/network details or normalize source identifiers/timestamps just to make a replay pass. Include local, remote, triage, C/A ordering, stale human block kind, retired requests, canonical header and consent boundaries. Inspect every resulting assertion and test exit code; missing dependencies or a timeout are not behavioral proof.

## Live proof

1. Read deployed hermes-mods SHA on the target Mac after normal sync; verify the change is in its ancestry. Read the live plugin version and exact painted connection/profile/session as well: checkout HEAD alone does not prove a backend imported new routes.
2. Use read-only DOM/store inspection and native `capturePage()` in the user's existing Desktop. Do not restart it, navigate it, type, open cards or click choices to manufacture proof. If the needed asks are not already visible, use an authorized hidden **owned** probe with disposable home/backend and pinned target identity, or leave native proof unobserved; a browser fixture is not the user's live Desktop.
3. Capture local, remote-gateway and triage asks at the same width. Record card ID, owner gateway/profile/board, lifecycle, model revision, request binding and deployed SHA per screenshot. Verify title, situation, own-letter ordering, detail, recommendation reason, Only you can and accessible consent Details. Matching tile layout is separate from remote answer delivery.
4. Inspect the actual PNG bytes with `vision_analyze`, attach all three screenshots to the task, and read back attachment identities. Restore/close any inspector the proof opened and verify the same Desktop process remains; clean only owned fixtures. Keep raw captures private.

## Pitfalls and verification

- Keep the user-steps installation owner (`install:<id>`) separate from the courier/sync gateway name. Membership rows and authenticated `gw:<name>` request routes use the latter; ActionStore namespace/operation owner use the former. Derive each from trusted local identity and exercise a producer-to-owner integration with deliberately distinct values. Equal fixture strings conceal a protocol that never mirrors or verifies a real operation.

- The left-pane **Blocked** sidebar section and the red row stripe have different inputs and different owners. Look for the Blocked section in core (`apps/desktop/src/plugins/kanban/blocked-section.tsx`, `fetchBlockedTasks` in `api.ts`), not in hermes-mods `desktop-plugins`. It lists every blocked card on every gateway and board. It groups each card by `managing_session_id` (the latest `tui` notify subscriber), else by the card's `session_id`, and gets group titles from `/session-labels` on the backend's own profile. The stripe turns red only from a visible row's `kanban_blocked`. When the section shows a card but no row is red, check the card's owner session on its home gateway (`home_gateway`; the local replica can be empty, so read the owner's DB through `tailnet_exec`). Cards created by background profiles have sessions the user's chats never show.
- A card the chat is waiting on is often not linked to that chat (for example, a worker created a review card). It then gets no row of its own, so the waiting row's line must carry that card's live status, not just its title. Otherwise running work looks idle. When the user asks "why isn't that card shown?", check `wait_target` and the hydrated `cards[id]` before you look at session linkage.
- Row UI preference: open a line row's own card by double-clicking its state square, not with an "Open card" button. Keep text buttons for other targets (parent card, CI, links).
- A push heartbeat proves transport freshness, not executor liveness. Do not reinterpret the cached model.
- An ask for an unfocused chat can exist in the backend without being painted; inspect both independently.
- After a hierarchy-to-flat migration, verify the actual flat rendering; absence of nesting is not proof that the old nested dedup case passed. Do not restore hierarchy merely to reproduce that test.
- Test recorded-decision dedup through both ask-row and line-row renderers: a live ask can share its card with an older answered-undelivered request. `showsDecision` must imply a drawn status line, and failed/unknown delivery must retain queue Check status/Retry even when it returns true. Exercise actual StepBox and mounted Panel actions, not only renderModel with a test-owned callback.
- Require a numeric, bounded replica sync timestamp before using a reported-running fallback; missing/future/non-finite values fail closed. Preserve real expired/non-running observation tests and kill the mutation that drops the producer-gap restriction.
- Use integer codepoint ranges for title emoji sanitization, not closed glyph regex lists: repo-wide runtime-notice guards intentionally reject private glyph classes. Keep Python and Desktop ranges in step and compare the replacement against the prior sanitizer over Unicode before claiming unchanged behavior.
- Passing jsdom proves structure, not native pixels; screenshots prove pixels, not that a click was recorded.
- For zero-work native acceptance, require both a zero-count authoritative model and absence of the painted panel/pending placeholder. A null renderer cache plus “Hub status model not available yet” is an availability failure, not proof the empty-panel fix works. Save the exact scoped feed response alongside the settled native capture; do not inject that model into the renderer to manufacture a pass.
- For native missing-model fallback proof, preserve the raw bundle task's absent `run_model`, its hydrated `cards[id].detail.events` route/tier model, and the same scoped rendered running row. A model label in pixels alone does not establish which source supplied it. For emoji proof, retain the exact raw title containing the offending codepoint and compare its corresponding model/DOM title and native PNG.
- Re-test a model-unavailable capture through a fresh owned window and normal hydration before calling the empty-state failure persistent. Preserve the earlier failure, but if the same zero-count revision reaches the native cache and the panel disappears without injection, report recovered acceptance separately from the earlier availability fault.
- Real answered asks may already be delivered by verification time. Read their operation dispatchState and report delivered-state dedup separately from the undelivered one-line status and failure-recovery branches; do not create or answer an unrelated ask just to obtain pixels.
- Report checks executed now, prior evidence reviewed, and production conditions still unobserved separately. Done means published skills, passing relevant gates, and the requested native captures attached—not a plan for obtaining them.

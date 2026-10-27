# Actual-plugin fixture acceptance report

## Packaged acceptance safety (required)

Live acceptance in the user's real Desktop window is read-only: screenshots and DOM/API reads only. Never synthesize input, click, focus, resize, change themes or open/close panels in that window. Do not restore or inject intercepted user text: its original target is unknown and cannot be recovered reliably.

Automated typing and keyboard checks belong only in an isolated test window/profile created and focused by the test. Verify that exact test window is frontmost immediately before every keystroke; abort on any mismatch. Never use the active window implicitly. Keep browser-harness input confined to its dedicated test browser, never a user's existing browser/profile. Record the window/profile identity with input evidence. VoiceOver remains unverified unless a separate isolated walkthrough is actually executed.

For chip acceptance, compare the same snapshot and clock through panel buckets and chip projection, not raw API count parity. Verify stale/unknown/failed and all held registrations are excluded from online/busy; the panel may still show running/draining separately. Screenshot readback cannot substitute for these semantic tests.

## Contract-faithful fixtures

An earlier review found the held fixture paired `not_checked` readiness with a fresh hold, which the backend cannot emit. Fixtures now mirror the merged `readiness_evidence` contract: every fresh hold is aggregate `fail` with its operational key failed and hold-explained (plus `service` for systemd drains), disk warnings stay independent, and the `?cases=1` set adds an independent tool failure under a drain, an unexplained service failure under a maintenance hold, and an unobserved operational key. The 1/3/8/45 partition is now reachable from real contract shapes. Harness results are still fixture evidence, not packaged or live-data acceptance.

## Latest executed results

- Node: **165 passed, 0 failed** (includes `readiness-evidence.test.mjs`, API count-parity and aggregate-consistency regressions). Owner backend suite `plugins/runners/tests`: all pass except `test_models.py::test_strict_validation_no_values[Observation-collected_at-not-time]`, which fails identically on unmodified `origin/main` in this environment.
- Browser `npm test`: **97 checks passed, 0 failed**, zero browser page errors; **23 captures**; three consecutive green runs. Machine evidence: local `report.json`.
- All fixtures are fictional public-contract data. These results do not claim deployment.

## Regression findings (fixed and rerun)

1. **IME arrow navigation:** with search focused, `compositionstart` followed by native `keydown {key:'ArrowDown', isComposing:true}` moves focus to a node. The input is not remounted, but focus is stolen. Chrome CDP composition/commit itself passes. The panel keyboard handler must guard native composition (React synthetic events may not expose `isComposing` directly).
2. **Modified slash:** Ctrl+/ from a node focuses search. Modified shortcuts should not be intercepted. Preserve the separate supported Shift+F10 native-menu path.
3. **Contradictory current-job description:** after the lazy owner-routed validated job response and successful exact-URL open, the row renders `Running — job 456 · Running — job not identified`. `details.test.mjs` retains the original invariant that a known current job must not claim to be unidentified. Preserve independent hold/warning reasons when correcting the generic activity reason.

The browser records independent failed checks and exits nonzero at the end; none is skipped or downgraded. Parent fixed composition/modifier guards and the validated-job display reason, then independently reran both suites successfully.

## Fixture integration

`sdk-mock.mjs` supplies an explicit simulated provider observation for the base runner; the `observe` helper adds fresh provenance only where tests explicitly simulate observations. Hold tests attach a real fictional host resource and independently timestamp its probe, using the deterministic evidence clock. Multi-runner fixtures enumerate provider observations and coverage totals. A former provider ID `0` in the 100-row fixture is replaced by valid positive IDs. Missing/stale provenance tests still explicitly remove or expire observations and remain fail-closed.

Labels/count assertions now use Idle, Running, Failed, warning-colored holds, independent stale provider/hold clocks, and scope total plus shown count. Host maintenance is asserted independently instead of applying a blanket registration hold. Owner routing, URL validation, conflict, stale evidence, lazy reads and unsupported action checks remain enforced.

Browser default fixture partitions **57** registrations into **1 running, 3 idle, 8 held, 0 failed, 45 unknown**; the 8 held rows also carry an independent disk warning, so Needs attention shows 53. One mapped node has 12 registrations; a second node is empty. The 45 unassigned registrations have explicit repository scope identities (30/15), not inferred hosts. Mixed-case fixture covers independent failure while held, unexplained service failure under maintenance, warning, expiry, stale observations, conflicting offline/busy, running/draining and offline/held.

## Verified browser coverage

- Real Chromium fast typing, middle insertion/deletion and caret position; CDP IME composition and commit; Tab; native selects; arrows, Enter, Space and layered Escape.
- Owner-routed lazy detail, no fleet-wide detail prefetch, native menu right click and Shift+F10, disabled unsupported maintenance, Escape focus restoration, registered palette callback and shortcut help.
- Every filter's shown count and sum of labeled bars; full 45-row unassigned cohort.
- Dark/light 900, 640 and 390 widths, at heights 900 and 480: no horizontal overflow, panel bounded by 70vh, internal inventory scroll, stable header/filter. All six expanded-detail theme/width combinations fit horizontally after details finish loading.
- Long physical-node list, offscreen trigger collision handling and custom accent/warning palette.
- Text contrast exceeds 4.5:1; state/focus indicators exceed 3:1 in tested dark, light and custom palettes. Exact measured ratios are in `report.json`.

## Screenshot inspection

Visually inspected `dark-900.png`, `light-640.png`, `dark-390.png`, `light-390-short.png`, `dark-390-short-expanded.png`, `all-cases.png`, `failed-detail.png`, `dark-900-detail.png`, `light-390-detail.png`, and `custom-390-short.png`.

The held hatch, idle outline, running solid segment and unknown dashed segment are distinct; labels/counts remain visible. Unassigned is an outlined full-width cohort. Narrow labels wrap without horizontal overlap. At short heights the inventory is intentionally cropped by its internal scroll area; header/search/state controls and footer remain visible. Loaded detail timestamps wrap, with evidence below the fold reachable through inventory scroll. An initial transient detail screenshot appeared clipped; rechecking after the lazy query settled and checking scroll geometry found no persistent detail overflow.

## Limits

SDK chrome/theme wrappers are approximations; this is actual plugin/React/Radix acceptance, not the installed Desktop shell. No live owner calls, production repair, native command-palette shell, OS-native IME candidate window or screen-reader walkthrough. Captures and machine reports stay local and ignored in this harness directory. Production signoff still requires the external acceptance gates documented in the design spec.

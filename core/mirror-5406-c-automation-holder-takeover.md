# Typed sends can reclaim automation-only session holders

Mirror entries: 5303, 5309. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

A typed prompt refused because another backend holds the chat only for a recognized automated turn can be flagged for Desktop's existing take-over-and-resend flow without a click. A typed holder, hidden/hosted automation request, unrelated refusal, unknown/missing session or unreadable store keeps ordinary refusal behavior. User-started loop ticks remain user work, not an auto-takeover target.

## How it works

The gateway inspects the newest active user row's display kind, using a bounded legacy-prefix classifier for older continuation rows. Recognized turn-starting notices (except `loop_tick`) plus `auto_continue` can flag `SESSION_NOT_OWNED` with `auto_take_over` and holder kind, only for a nonhosted typed submit. The legacy classifier is reached through the rebound server facade (5309).

Desktop stamps only flagged gateway refusals with the refusal time, preserves owner/error surface details and invokes its existing takeover action once for a fresh card. Freshness is 30 seconds and per-stored-chat cooldown is 120 seconds; stale transcript cards and repeat grabs retain the manual button. Recording the attempt prevents rerender/remount from immediately repeating it.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts`
- `apps/desktop/src/app/session/hooks/use-prompt-actions/utils.ts`
- `apps/desktop/src/components/assistant-ui/thread/assistant-message.tsx`
- `apps/desktop/src/lib/error-surface.ts`
- `tui_gateway/methods_prompt.py`
- `tui_gateway/session_lifecycle.py`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Gateway source tests cover newest-live-row reads, real temporary SessionDB classification, legacy continuation kinds, typed/hidden/error/missing cases, loop exclusions, slot/turn-claim/prompt-submit refusal flags. Desktop helper/component tests cover flagged surfaces, freshness/cooldown, exactly-once takeover/resend and stale/unflagged manual behavior. Entry 5309 is a split-module correction with no new test in that mail; the listed classification suite is source coverage, not proof this mirror ran that fix.

Relevant source suites/fixtures:

- `apps/desktop/src/app/session/hooks/use-prompt-actions/auto-take-over.test.ts`
- `apps/desktop/src/components/assistant-ui/thread/assistant-message.test.tsx`
- `tests/tui_gateway/test_automation_holder_takeover.py`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

This does not transfer ownership for every refusal or prove a two-window live race is impossible. Cooldown is local UI attempt state, not global ownership consensus. Real reconnect/multi-window takeover and provider turn execution are untested.

## Why core instead of a plugin

The gateway must classify the actual holder and communicate a typed refusal; Desktop must bind that signal to its takeover UI. A plugin should not infer ownership from error prose or silently seize a chat on its own.

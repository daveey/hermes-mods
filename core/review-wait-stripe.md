# Sessions waiting on a code review stripe red, or grey while others were asked < 24h (patch 0143)

Patch:

- `patches/0143-feat-desktop-sessions-waiting-on-a-code-review-strip.patch`: a new
  `hermes_cli/review_session_activity.py`, one-line call sites in `hermes_cli/web_routers/sessions.py`,
  `hermes_cli/web_routers/profiles.py` and `tui_gateway/methods_projects.py`, and the Desktop stripe
  (`store/session-dot-state.ts`, `session-row.tsx`, `session-tab-accent.tsx`, `sidebar-visible-order.ts`,
  `lib/session-signatures.ts`, `types/hermes.ts`), plus tests.

What you see: a chat whose last reply links a GitHub PR that is still waiting on a code review no longer looks
finished (green) or busy (no stripe, because a CI watcher is running). Its sidebar row and tab get a stripe:

- **red** ("stuck") when the review is on you: you are a requested reviewer, nobody has been requested at all,
  or the other reviewers were asked more than 24 hours ago;
- **grey** ("blocked", like ⌘B) when only other people, teams or bots were requested in the last 24 hours.

This extends the sidebar status stripe (`sidebar-status-stripe.md`).

## Behavior

- Only the session's **latest assistant message** is scanned, across its compression lineage. Up to 5 GitHub
  PR URLs (`https://github.com/<owner>/<repo>/pull/<n>`) are considered.
- A PR counts when it is open, not a draft, and `reviewDecision == REVIEW_REQUIRED`, and either you (the `gh`
  viewer) authored it or it requests your review. Someone else's PR that merely gets cited changes nothing.
- The state rules, in order: you are a requested reviewer → red. Otherwise, a PR you didn't author → no
  stripe. No reviewer requested → red. The newest review request for a still-requested reviewer (or the PR's
  creation time when there is no such event) is under 24 h old → grey. Otherwise → red. With several PRs, red
  wins.
- Priority in `sessionStatusStripe`: a working turn always wins (no stripe). Needs-input, stalled and blocked
  Kanban stay red. ⌘B (manually blocked) and the periodic cron loop still win over a review stripe. A review
  stripe beats a running background watcher and "done".
- ⌘] / ⌘[ stop on red rows and skip grey ones. Stepping and the stripe share `sessionHasStatusStripe`.
- A later reply without the URL clears the stripe, and so do approving, merging, closing or converting the PR
  to draft (within the cache TTL).

## How it works

- `annotate_review_wait(rows)` stamps `review_wait` (`"stuck"` / `"parked"`) and `review_wait_pr` (the PR URL)
  on rows, and removes stale stamps. It is called on `GET /api/sessions`, `/api/profiles/sessions`,
  `/api/profiles/sessions/sidebar` and the project tree rows (`tui_gateway/methods_projects.py`). It never
  raises.
- Messages: the last non-empty assistant message is read read-only (`mode=ro`) from the row profile's
  `state.db` over the row's id, `_lineage_root_id` and `_lineage_ids`. The extracted PR keys are cached per
  row id and keyed by `(last_active, message_count)`, so an unchanged row costs no query.
- PR state: `gh api graphql` (viewer login, state, draft, reviewDecision, author, `reviewRequests`, and the
  `REVIEW_REQUESTED_EVENT` timeline for request times) runs on a daemon thread. Results are cached for 5 min,
  or 2 min after an error. A list call never waits on GitHub, so a row gets its stamp on the next poll after
  the lookup lands. `gh` comes from PATH, falling back to `/opt/homebrew/bin`, `/usr/local/bin` and `/usr/bin`.
- Desktop: `sessionStatusStripe(state, ..., periodic, reviewWait)` gains a 6th argument. `stuck` joins the red
  branch when not working, and `parked` maps to `'blocked'` (grey) when not working. Every caller passes
  `session.review_wait`. `sameCronSignature` compares `review_wait`, so a change repaints the sidebar.

## Code

```python
# hermes_cli/review_session_activity.py
def review_state(info, now=None):
    if not info or not info.get("open") or not info.get("review_required"):
        return None
    me = f"user:{info.get('viewer') or ''}"
    requested = info.get("requested") or []
    if info.get("viewer") and me in requested:
        return "stuck"
    if not info.get("viewer") or info.get("author") != info.get("viewer"):
        return None
    if not requested:
        return "stuck"
    since = info.get("requested_at")
    return "parked" if since is not None and (now or time.time()) - since < PARK_WINDOW_S else "stuck"
```

```ts
// store/session-dot-state.ts
: state === 'needs-input' || state === 'stalled' ||
    (kanbanBlocked > 0 && state !== 'working') || (reviewWait === 'stuck' && state !== 'working')
  ? 'stuck'
  : reviewWait === 'parked' && state !== 'working' ? 'blocked' : ...
```

## Tests

- `tests/hermes_cli/test_review_session_activity.py` (19 tests). PR URL parsing: dedupe and lowercase, and an
  issue URL is ignored. Every state rule: review requested from me, nobody requested, others requested 1 h and
  25 h ago, a team reviewer, the PR's creation time used when there is no request event, someone else's PR asking
  me, and someone else's PR not asking me. The not-waiting states: merged, closed, draft, approved, changes
  requested, no decision. Annotation: the newest message across the lineage wins, a later reply without the URL
  clears the stamp, and a PR mentioned only by the user is ignored. A second poll does not re-fetch the PR. A fetch
  error clears an old stamp. The async path returns in under 1 s with no stamp, then stamps once the lookup lands.
- `apps/desktop/src/store/session-dot-state.test.ts`: `[daveey] a PR waiting on my review is red; on others for
  <24h is grey; working wins`. It covers idle/unread/background with both states, working with both (no stripe),
  needs-input staying red, ⌘B winning, and `null`/`undefined` leaving done.

```sh
scripts/run_tests.sh tests/hermes_cli/test_review_session_activity.py
cd apps/desktop && npx vitest run src/store/session-dot-state.test.ts
```

The Python tests and `session-dot-state.test.ts` (38 tests) passed on the full 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105, 0106, 0107, 0108, 0109, 0110, 0111, 0112, 0113, 0114, 0115, 0116, 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125, 0126, 0127, 0128, 0129, 0130, 0131, 0132, 0133, 0134, 0135, 0136, 0137, 0138, 0139, 0140, 0141, 0142, 0143 series at merge-base `4957bbd0a0` (Oct 19, 2026 PT). A live probe
against a real `state.db` stamped a session linking a PR with one other reviewer requested a few hours earlier as
`parked`. Untested: the REST and project-tree routes end to end. Only `annotate_review_wait` is tested, and
the call sites are one-line additions.

## Applying

`git am -3 --empty=keep core/patches/0143-*.patch`. It edits the same `sessionStatusStripe` signature and the three callers as
the earlier stripe patches (`sidebar-status-stripe.md`: blocked Kanban count, ⌘B, periodic cron) and the
`annotate_*` chain in `web_routers/profiles.py` from the background-activity patches, so apply the series in order.
A running backend keeps the old router until it restarts, and the Desktop needs a rebuild.

## Gotchas

- It needs `gh` installed and authenticated on the host running the backend. Without it there are no stamps
  and no errors.
- Only the latest assistant message counts. Mentioning the PR in an earlier turn is not enough.
- The first poll after a backend restart, or after a reply, has no stamp yet. It appears one poll later, and
  it can lag a real review by up to the 5-minute cache TTL.
- "Others asked over 24 h ago → red" uses the newest request for a still-requested reviewer. Re-requesting a
  review resets the 24 h window.

Plugin? No. The stripe predicate is core Desktop store code shared with ⌘]/⌘[ stepping, and the session rows
come from core routers with no row-annotation hook.

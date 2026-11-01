# Trusted producer completions for unrouted inline sessions

Patch: **1532** (`patches/1532-feat-tui-trusted-durable-producer-completion-ingress.patch`, fork `c4565ec4d728`).

## Behavior

Before this patch, an inline TUI or Desktop chat with no gateway route and no local policy receipt (an older inline
session) had no durable way to receive a **producer completion**, such as a finished user-terminal job. The
existing ways in did not fit:

- `admit_automation` needs a route.
- Legacy adoption needs an authenticated native transport, and it refuses a chat whose inline owner is still live.
- `plugin_inject` would create ordinary user input.

This patch adds a durable ingress for that case:

- **Admission API (in-process only, no RPC).** `hermes_state_producer_admission.admit_producer_completion(producer=,
  profile_home=, stored_session_id=, text=)` writes one outbox row per producer identity into the profile's own
  `state.db`. The receipt `outcome` is one of:
  - `admitted`, with `status` `queued`, `committed` or `dispatched`.
  - `routed`: the chat has, or has since gained, a gateway route or local receipt. Nothing is delivered here; the
    caller should use `admit_automation`.
  - `missing`, with a reason: `not_found`, `wrong_profile`, `unknown_profile_home`, `not_local` (a messaging
    session) or `session_deleted`.
  - `retry`: storage was busy or unavailable.

  `read_producer_admission` returns the current receipt, or `{'outcome': 'absent'}`.
- **Idempotent per identity.** Admitting the same producer identity with the same text again returns the stored
  receipt and creates no second row or turn. The same identity with different text raises `ValueError`.
- **Only trusted producers.** The producer identity must be a compact JSON array of at least two non-empty
  printable strings whose first element is in `TRUSTED_PRODUCER_KINDS`; today that set is only `user-terminal`.
  Text must be non-empty, at most 16000 characters, and free of NUL bytes.
- **Delivered only by the owning inline runtime**, through the notice channel:
  - The chat's notification poller calls `deliver_pending(sid, session)` on every kanban poll.
  - A busy chat, or one where the user pressed Stop, leaves the completion queued (`busy`).
  - Another live owner refuses delivery (`refused`) and is never taken over (`allow_takeover=False`).
  - Otherwise ONE `process_complete` notice row is written on the conversation's live compression tip, and the row
    is marked `committed` in the same transaction. It then runs as a synthesized turn that adopts that row: no
    `turn_author` and no second user row.
- **Producer text is data.** `@` references in it are not expanded into model context.
- **Restarts and races are safe.**
  - A refused dispatch leaves the row `committed`, and the next pass re-stages that same message row.
  - A committed row followed by an assistant message is treated as already run: it is marked `dispatched` and not
    run again.
  - When two deliverers race, one writes and the other gets `committed` with the first one's row id.
- **Cold chats.** In a Desktop or `serve` backend, a sweeper runs every 15 s. For each profile store it wakes a cold
  chat that holds a queued completion with an in-process `session.resume` (`omit_messages`, plus `profile` for a
  named profile), then delivers. A refused resume leaves the row queued for whichever backend holds the chat.
- **A chat adopted after admission** has its queued row retired as `routed` (`rerouted`) and never delivered here.
  A chat deleted after admission is reported `missing` / `session_deleted`.

## How it works

- **Storage.** The `producer_admissions` table in the session's own profile store is keyed by `producer`. Each row
  records the owner (the compression root, i.e. `_session_turn_lease_key_on_conn`), a payload SHA-256 digest, the
  status and the delivery ids. `open_profile_db` opens only `<profile_home>/state.db` and returns `None`, with no
  fallback store, when that file is missing or is not a Hermes profile store.
- **Routing check.** `route_reason` walks the owner's compression chain:
  - A foreign `profile_name` gives `wrong_profile`.
  - `session_key`, `chat_id` or `origin_json` gives `gateway_route`.
  - A source outside `LEGACY_LOCAL_SOURCES` gives `not_local`.
  - A `POLICY_PREFIX` or `LEGACY_PREFIX` key in `state_meta` gives `local_receipt`.

  `wrong_profile` and `not_local` refuse admission. `gateway_route` and `local_receipt` route it.
- **`commit_delivery`** runs in one writer transaction. It re-checks status, existence and route, and returns
  `moved` when the conversation's tip is not the caller's target. It enforces the transcript write guards
  (`reject_active_turn_lease=True`), reopens an ended tip, inserts the staged message and marks the row
  `committed`. While another backend runs a turn it raises `SessionTurnLeaseLostError`, which the deliverer reports
  as `busy`.
- **Delivery.** `tui_gateway.producer_admission.deliver_pending` takes the notice turn claim (`_notif_claim_turn`)
  and runs the owner refusal checks (`_kb_owner_refusal`, `_ensure_active_session_slot(allow_takeover=False)`). It
  then commits the row or re-stages it, sets `_submit_user_row` and `_marker_until_tool`, and calls `_notif_submit`
  with `display_kind=process_complete` and `display_metadata.producer_admission`. `_run_admitted_prompt` sets
  `_TurnRun.literal_text` from that metadata key, which makes `_prepare_turn_input` skip
  `preprocess_context_references`.
- **Wiring.** The sweeper (`start_producer_admission_sweeper`, idempotent) is started from
  `web_server_app.standalone_lifespan`. The poller hook is added to `_notification_poller_scoped_loop`.

## Code

- `hermes_state_producer_admission.py` (new): admission, receipts, routing and the delivery-side store helpers.
- `tui_gateway/producer_admission.py` (new): `deliver_pending`, `sweep_once` and the sweeper thread.
- `tui_gateway/prompt_turn.py`: `literal_text`.
- `tui_gateway/session_notifications.py`: the poller hook.
- `hermes_cli/web_server_app.py`: sweeper start.

## Tests

`tests/tui_gateway/test_producer_admission.py` (new, 438 lines) uses the real `SessionDB` and the real
poller/sweeper delivery functions. Only `_run_prompt_submit` (the agent turn), `_emit`, and
`_ensure_active_session_slot` (in the fixture) are stubbed, and `_enqueue_prompt` asserts it is never called.
**The tests were read from the diff and not executed in this mirror pass.**

- **Idle owner:** one notice row and one turn, adopting the row, with no `turn_author`; the status becomes
  `dispatched`, and the next poll is `none`.
- **Busy, stopped and foreign owners:** a busy owner stays queued until idle; a stopped owner (`busy`) starts no
  turn; another live owner is `refused` with `allow_takeover=False` and the claim is released.
- **Sweeper:** wakes a cold chat once and the next sweep is empty; a refused resume stays `queued`; a named-profile
  chat is resumed with `profile: "work"`.
- **Compression lineage:** the owner is the root; a stale runtime gets `moved` and the live tip gets the row;
  rotation between admission and delivery uses the new tip.
- **Missing cases:** `not_found`, deleted after admission, wrong profile home, a row of another profile
  (`wrong_profile`), `unknown_profile_home`, a messaging session (`not_local`).
- **Idempotency:** a lost-receipt retry returns the same receipt; a different payload raises; a refused dispatch
  re-delivers the same row; a turn that ran before a crash is `settled`, not rerun; two deliverers race on one row.
- **Routing:** a routed session gives `gateway_route` and writes no row; a chat adopted after admission is
  `routed` / `local_receipt` and not delivered.
- **Validation:** untrusted or non-compact producer identities raise; empty, over-length and NUL texts raise.
- **Literal text:** `see @/etc/passwd` reaches the turn unexpanded.
- **Wiring:** the poller calls `deliver_pending`; the sweeper starts once.

**Untested:**

- The `retry` (storage busy or unavailable) outcome.
- The `build_failed`, `no_resume` and `not_live` sweeper verdicts.
- The `web_server_app` lifespan start.

## Applying

Apply in series order (`git am -3 --empty=keep`). The patch depends on the split `tui_gateway` notice machinery
(`_notif_claim_turn`, `_notif_submit`, `_submit_user_row` adoption, `_readopt_wake_profiles`), the session-lease and
compression-tip helpers in `hermes_state`, `hermes_state_local` and `hermes_state_local_migration`, and
`tui_gateway.automation_replay` (which `prompt_turn` already imports at that line). No config is needed.

## Gotchas

- **No producer calls it yet.** At this fork tip, `admit_producer_completion` is referenced only by its own module
  and its tests. The patch adds the ingress contract and the delivery path; the user-terminal job that would call
  it is not wired in this patch.
- **Callers handle `routed`.** A `routed` receipt means "use `admit_automation` with the same identity"; nothing is
  delivered here.
- **The table is created lazily.** `producer_admissions` is created on the first admission, not by the session
  schema migrations.
- **Only the sweeper reaches cold chats.** In a plain TUI with no Desktop/serve backend, a cold chat is delivered
  only after it is opened, because the sweeper starts only in the web server lifespan.

## Plugin?

No. This is a core state and TUI-gateway change. A plugin could become a producer only if core adds its kind to
`TRUSTED_PRODUCER_KINDS`.

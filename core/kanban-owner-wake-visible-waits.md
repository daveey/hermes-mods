# Owner wake and visible waits: what each parked card waits on, amber "stalled"

Patches **0544–0547** (core), plus the `chat-closeout` plugin's owner wake (`plugins/chat-closeout/owner_wake.py`)
and the `session-tasks` desktop plugin's wait lines.

## Behavior

On 2026-10-26 a remote card's `continuation_exhausted` notice landed in its owning chat and started no turn. The
chat's tracker had narrowed its wake to done/review/triage, and the sidebar said "waiting on N cards, 0 blocked" for
about 5 h while nothing was producing what those cards waited for. Archived and reaped chats still owned open cards.

Now:

- **Every parked card says what it waits on.** `scheduled`, `blocked` and `triage` cards carry a `wait` object:
  `waiting_on` (e.g. `card t_x on hub`, `ci CI run`, `the clock until 2026-10-28 09:00 UTC`, `an agent to clear a
  capability block`), `waiting_since`, `agent_owned` (nothing outside the agents will end it), `wait_state`
  (`human` = your move, `stalled` = agent-owned for over 2 h, else `waiting`) and a stable `premise` key.
- **Amber "stalled".** A chat whose parked cards include an agent-owned wait older than 2 h gets an amber sidebar
  stripe and an amber watch-glyph count (`kanban_stalled`), distinct from grey waiting and red needs-you (precedence: red >
  amber > grey). The session-tasks strip shows "waiting on <producer> since <time> (<age> ago)" per card and an
  amber "stalled" pill.
- **Owner wake** (chat-closeout plugin). A card that a chat created or shepherds (local, or remote via the origin
  projection) parking for an agent-owned reason starts ONE supervise turn in that chat. Once per card per premise,
  at most 3 per chat per hour, at most 3 attempts per tick and 10 chat wakes per hour gateway-wide. An archived,
  reaped (`startup_orphan_reap`) or missing chat routes the cards to a board-supervisor card instead (at most 6 per
  hour). Waits older than 48 h when first seen are recorded, never woken.
- **Tracker default.** `hermes kanban wait-check task <id>` with no `--until` is met on
  `done,review,blocked,triage,parked-agent,archived` (`parked-agent` = scheduled/blocked on an agent-owned wait).
  Run as a dispatcher probe, it is not met again while the target still sits in the status the waiter's last
  `condition_met` already reported (implicit `--from`), so a tracker that handled a wake and re-parked waits for the
  next change. `--from <statuses>` sets that baseline explicitly.

Agent-owned: a `transient`/`capability`/`assignment` block, triage after `gave_up`/`continuation_exhausted`/
`supervisor_escalated`/`triage_swept`, `continuation_exhausted`, a park declaring `awaiting.kind=human` (a hidden
ask), or a declared awaiting with no producer. Uncertain (counts only once stalled): an untyped block, a park on an
opaque probe. Not agent-owned: a dependency block, a needs-input block, block-loop triage, a park on a card / CI run
/ deploy / external ref / clock window. On the "no producer, no park" core (0529) every park records an `awaiting`;
an **inferred** clock/external guess is judged by its wake mechanism (wake_check / wake_at), not as "no producer".

## How it works

- `hermes_cli/kanban_wait_info.py` (new): pure `describe(status, block_kind, human, park, park_kind, since, awaiting)`
  and `describe_replica(card)` for remote rows; `premise_key` hashes status + kind + reason code + awaiting
  (kind, ref, gateway), and the normalised reason text only when nothing structured names it.
- `hermes_cli/kanban_session_activity.py`: `_local_wait_info(conn, ids)` reads each card's newest park event;
  `_read_stalled` feeds `kanban_stalled` on session rows (same `_all_counts` snapshot); session task rows carry
  `wait`; `parked_waits()` lists every parked card per owning session (local boards + origin projection; a failed
  remote read keeps the local cards).
- `hermes_cli/kanban_wait_check.py`: new default, `parked-agent`, `--from`, `handled_status` (gated on
  `HERMES_KANBAN_WAKE_PROBE=1`, which `kanban_wake_check._probe_env` sets for probe runs).
- Desktop: `store/session-dot-state.ts` `kanbanStripeCounts` returns `stalled`; `lib/session-status-stripe.ts` adds
  the amber stripe; `session-row.tsx` renders the amber `watch` codicon count in both trailing branches; `sidebar.row.kanbanStalled` i18n key.
- Plugin `chat-closeout/owner_wake.py`: claims live in `<kanban home>/cache/chat-closeout/owner-wake.db` (INSERT per
  card+premise, so two backends never both wake); busy releases the claim, refused/error keeps it until
  `CLAIM_TTL_S` (retry backoff); an unreadable state store is "unknown" (skip), never "gone".

## Tests

`tests/hermes_cli/test_kanban_wait_info.py` (classification, inferred awaiting, stalled stamping, tracker default,
implicit `--from`, remote read failure), `apps/desktop/src/app/chat/sidebar/session-row.test.tsx`,
`apps/desktop/src/store/session-dot-state.test.ts`; plugin `plugins/chat-closeout/tests/test_owner_wake.py` and
`desktop-plugins/session-tasks/test/instant.test.mjs`.

## Gotchas

- The plugin needs this core (`kanban_session_activity.parked_waits`); on an older core the owner wake is a no-op.
- `sidebar.row.kanbanStalled` is listed in `overlay-gaps.json` like its kanban neighbours (untranslated overlays).
- ⌘] / ⌘[ still step only to green/red stripes; amber stalled is not a stop.

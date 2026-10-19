# Follower -> Mac session delivery (slack_defer_to_mbp)

A follower on nous-1 that needs the Mac queues a request (`eng_mbp_queue`) addressed to the
engagement's owner session, e.g. `@session:default@this-device/<id>` (the MacBook's peer.json has
`self_gateway: this-device`). Two ways deliver it:

- courier: the bot gateway's tick writes a session-mentions request into nous-1's outbox and the
  MacBook's Desktop (which drains nous-1 as courier `david-mbp`, later `macbook`) submits it through the
  session's owning socket.
- pull: `~/.hermes/scripts/slack_follow_pull.py`, a no-agent cron on the MacBook every 2 min, fetches
  requests (op `mbp_pull` over MacBook -> ssh mini -> cred-relay shell.exec on nous-1) and starts a
  detached `hermes chat --resume <id> --yolo` turn.

## Decision (2026-10-19): courier first, pull as the fallback

Policy `follower.mbp_delivery`: `courier` (default) | `courier_only` | `pull`. `follower.mbp_device`
(default `david-mbp`) names the Mac for owner refs that say `@this-device`/`@local`.

Pull was chosen earlier only because the courier could not target one Mac or a named profile. Both
are fixed (session-mentions DELIVERY.md, "Named profiles on a cloud gateway"). The pull cron is not
strictly better:

- Courier delivers into the chat the way a typed message does: through the owning Desktop socket,
  queued behind a running turn, visible live in the open window, the ownership lease kept. Pull runs
  a second process on the same session. If David is mid-turn there, the two turns interleave in one
  transcript, the Desktop window doesn't show the new turn live, and the turn runs `--yolo`.
- Courier latency is the bot tick plus a ~3-5 s drain. Pull adds up to 2 min of cron and a slow ssh +
  chunked shell.exec chain.
- Pull needs the mini up and nous-1's shell.exec plus free disk on `/opt/data` for every poll, every
  2 min, even when idle. On 2026-10-19 it failed 6 times (disk full, `FileNotFoundError` /
  `JSONDecodeError` from the chunked upload). The courier only writes when there is a request.
- Pull's one advantage is real: it works when the Mac is awake but the Desktop app is not running.
  Neither works while the Mac sleeps; both deliver after wake. So pull stays as the fallback.

How the two share the queue without double delivery:

- The courier claims a row atomically (`mbp_claim_one`, a conditional UPDATE) before sending. The
  request carries `route` = the Mac's name, so only that Mac's courier claims it (another Desktop
  reaching it over SSH would be refused while the chat is open on the MacBook).
- Courier failed cleanly (no courier picked it up in 25 s, or `refused`): the row is due at once for
  the puller, and the courier backs off 5 min.
- `mbp_pull` in `courier` mode only hands out rows the courier already tried, or older than 3 min
  (`PULL_GRACE_S`, covers a stalled bot tick). It claims each one atomically too.
- Courier outcome unknown (`status: unknown`, or claimed with no result): the row becomes
  `uncertain` and is never resent by either path.

Tests: `tests_follow.py` (route stamp, this-device rewrite, courier then pull fallback with no double
claim, grace, `courier_only`, unknown outcome not resent).

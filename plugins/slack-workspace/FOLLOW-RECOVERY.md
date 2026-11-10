# Follower failure recovery

Existing engagements resume their stored follower session without creation-policy overrides.
The gateway owns the session's frozen model, tools and approval policy. Research-model selection
therefore applies only when creating a session, not as an override on a resumed one.
Both legacy `session_id:` and current `Session:` CLI footers are retained.

Failed kickoff, note and message turns keep their queued work. `eng_turn_failures` stores the
full-output fingerprint, consecutive count, next attempt time and signal latch in the observation
SQLite database. The first two failures wait 60 seconds; from the third identical failure the wait
starts at 5 minutes and doubles up to one hour. Process restarts do not reset it. Success clears the
budget; a different error starts a new streak. Random query filenames are normalized before hashing.

At the third failure one ERROR record starts `SLACK_FOLLOW_STALLED`. Coach's errors collector accepts
this record only from `hermes_plugins.slack_workspace.follow` and turns it into a plugin self-heal
signal, including engagement and diagnostic head. Ordinary warning text is not an incident detector.
Stderr is retained before stdout and failure logs include the first 1,500 characters, not a banner tail.

Tests: `tests_follow.py` covers all three turn branches, restart persistence, delay cap, single signal,
changed-error rearming, exception handling, success reset, frozen resume argv and diagnostic head.
`plugins/coach/tests/test_signals_slack_follow.py` covers signal conversion, profile-log collection,
cursor dedupe and false-positive boundaries.

Deployment caveat: the bot runner has a long-lived ticker. Plugin rediscovery alone does not prove
that an existing ticker changed code. Verify activation or use the gateway's supported idle/paused-turn
restart path before restoring a temporarily paused engagement. Restore via `set_state(..., 'active')`
when queued work must remain: the public `resume` operation deliberately skips the paused backlog.

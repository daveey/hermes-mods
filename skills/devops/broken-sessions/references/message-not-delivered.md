# Chat isn't taking the user's messages

The Session repair scanner can't see this one. The cause is in the Desktop-to-hub transport or client, not in card
state, so skip the scanner and go straight to the logs.

1. Check whether the message reached the hub. In the hub's `~/.hermes/logs/gateway.log`, grep
   `tui prompt accepted ... agent_session_id=<session id>` and the `tui turn finished` lines around it. If there is a
   gap between the last turn finishing and the user's prompt being accepted, the message was held on the client side.
2. Find out why. Read the Mac's Desktop log over `tailnet_exec host=mbp`. It lives at `~/.hermes/logs/desktop.log`,
   not under `~/Library/Logs`. In the gap window, grep for `Timed out connecting to Hermes backend`, 503s
   (`sessions projection unavailable`, `facts_unavailable`), `silent-turn ... request_failed`, and
   `Chat is not owned by this runtime; reconnect and retry`. The last one means the client kept a ui_session the
   backend no longer owned, and it does not reconnect on its own. Log times are in PST.
3. Check the current state. If the session's newest messages show a turn running now, the message got through and
   no chat repair is needed. When a MBP-to-hub transport card is already open, add the evidence there as a comment.
   Reopening the chat gets it a fresh runtime if it is still stuck.

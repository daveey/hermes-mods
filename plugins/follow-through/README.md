# follow-through

Deferred "when card X finishes, do Y" deliveries actually happen, once, and are reported honestly.

## Why

A coordinator chat was told "when the Mac installer is ready, send it to her on WhatsApp". It spawned the builder
card, told it *not* to send ("the parent session sends it") and kept the delivery in chat memory. The builder
finished with "invite minted but NOT sent", and nothing sent it until the user asked twice. Causes found:

1. The completion notice never reached the chat: the chat was open in two backends, the non-owning one claimed the
   notice and then had its turn refused (`SESSION_NOT_OWNED`), dropping it. Fixed in core (see
   `core/` docs, "kanban notice refusal rewind").
2. The obligation lived only in conversation context. After compaction, the summary still listed it, but under a
   preamble saying not to act on summarized requests.
3. Asked "did you send her a link?", the model admitted the miss without doing it.
4. After finally sending, the readback was empty; that must be reported as "sent, not verified".

## What it does

| Hook | Behavior |
|---|---|
| `transform_tool_result` on `kanban_create` / `kanban_comment` | When a card body or comment records that the **parent** will deliver something ("do NOT send it yourself", "the parent session sends it"), appends a steer to schedule it now as a dependent card: `parents=[X]`, `idempotency_key='followup:<X>:<action>'`, with the no-duplicate + verification protocol. The dispatcher runs that card by itself when X completes, even if the chat never sees the notice. |
| `pre_llm_call` (conditional, no tokens otherwise) | For completed cards this chat is subscribed to whose handoff is explicitly undelivered ("minted but NOT sent") or whose completion notice never reached the chat, and that have no `followup:` card and no `follow-through: SENT` marker comment: injects the handoff plus the recorded instruction (quoted from the card body/comments; capability links redacted) and a do-it-now protocol. Throttled per chat+card (30 min, max 6, on disk). |
| `pre_llm_call` on a missed-action question ("did you send…?", "how am I supposed to…") | Injects: do the still-authorized action now, verify, then answer. An admission alone is not an answer. Bypasses the throttle. |
| `transform_tool_result` on messaging readback tools | After a successful `*send_message`/`send_file`, an empty `list_messages`/`get_chat` readback gets "delivery NOT verified; don't resend because of this alone". |

It never sends anything and never widens what was authorized: the reminder quotes the instruction that was
recorded on the card and tells the agent to ask when none was recorded.

Convention: whoever performs a deferred delivery records
`kanban_comment(task_id=<X>, body='follow-through: SENT via <channel> at <time>; delivery verified|not verified')`.
That marker is the retry/duplicate guard and clears the reminder (`DELIVERED`, `DONE`, `CANCELLED` also count).

## Tests

```
cd plugins/follow-through && uv run -q --with pytest python -m pytest -q -p no:cacheprovider tests
```

Fakes only (throwaway SQLite boards/state.db built from `tests/kanban_schema.sql`, fake WhatsApp results); nothing
is sent.

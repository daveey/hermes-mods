# WhatsApp: how an agent gets onto it

Use when the user asks which WhatsApp route to use for an assistant (messaging people, letting some people talk to it). Check first whether a bridge already exists (SKILL.md rule 0b); Baby Bear already has one on its own number.

| | Native third-party agents (in-app, API key per agent) | Business Platform / Cloud API | Business app + linked whatsmeow device |
|---|---|---|---|
| Agent messages other people | No: it only sees its own 1:1 chat with the user | Only template messages outside the 24 h window | Yes |
| Other people talk to it | No: tied to the user's account, 1:1 only | Yes | Yes |
| Groups | No | No | Yes |
| E2E | No | Partial | Yes |
| Policy | Allowed (rolling out, max 5 agents) | General-purpose AI assistants banned by Meta's terms | Unofficial client; ban risk lands on that number only |
| Cost | Free | Per message + business verification | Free |

Default recommendation: give the assistant its own number registered fresh in WhatsApp Business (never Change Number, SKILL.md rule 0a) and link a whatsmeow bridge to it. Native agents are only another private channel to the user; the Cloud API fits customer-service bots, not a personal assistant.

Access tiers to offer on top of the bridge: **engaged** contacts (inbound becomes full turns), **contactable** contacts (agent may message after user confirmation; replies relayed to the user), unknown senders relayed rather than dropped, groups opt-in per group.

Re-registering or re-logging the Business app on the phone removes its linked devices; the bot bridge then sits in pairing mode and must be re-scanned before any send.

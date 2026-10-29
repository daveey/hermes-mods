# Find a thread across the bot's channels

Use this for an owner-authorized search when the topic is known but the channel is not.

1. Locate the bot profile on its owning gateway. Search its `slack-workspace/observed.sqlite` read-only rather than fetching every reply from a familiar channel. Inspect `sqlite_master` first to confirm the schema.
2. Query distinctive literal topic fragments, returning only routing fields and text:

```python
import sqlite3
c = sqlite3.connect('file:' + database_path + '?mode=ro', uri=True)
c.row_factory = sqlite3.Row
rows = c.execute(
    'SELECT channel, ts, thread_ts, user, text FROM messages '
    'WHERE deleted = 0 AND lower(text) LIKE ?',
    ('%' + topic.lower() + '%',),
).fetchall()
```

Broaden synonyms if needed. The index covers only observed messages; it is a locator, not authoritative current thread state.

3. For a hit, use `thread_ts or ts` as the root. Fetch `conversations.info`, paginated `conversations.replies`, and `chat.getPermalink` through the authorized bot transport. Keep credentials on the owning host. Return compact message fields, not duplicate Slack block markup.
4. Read all live replies before starting work: they may contain an existing owner, approval, correction, or failed prior attempt absent from the observation index. Fetch nearby channel history only when needed for source links or context.
5. Report the verified channel and permalink. If the index has no useful hit, discover channels and search live history with pagination; do not treat a familiar channel's bounded dump as a workspace-wide search.

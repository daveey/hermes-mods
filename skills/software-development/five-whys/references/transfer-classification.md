# Held-task transfer classification

Replay the actual resend write set in a rolled-back transaction and compare fence refusals to preview eligibility. Include incoming versus outgoing edges, deleted history, malformed/orphan markers and reserved events already fenced; row ownership alone misses related-row refusals.

Keep default selection stable, make skipping explicit with an exact expected set, classify before independent recovery effects, and recheck after every await. Report committed recovery separately from ownership transfer; a later refusal cannot undo an earlier committed phase.

Bind replay epochs when native intent is captured, not when queued or reconstructed operations are sent: assigning the current epoch at resend legitimizes historical intent. Prove rejection after the former home obtains a later genuine execution lease; lease custody and administrative home are independent.

Before attributing a SQLite REPLACE bypass to an alternate identity, inspect PRAGMA index_list/index_xinfo and prove the conflicting index is UNIQUE. A nonunique idempotency index allows a second insert without deleting the protected row. Model a future unique identity explicitly when testing generic replacement fencing, and distinguish that fixture from a current production defect.

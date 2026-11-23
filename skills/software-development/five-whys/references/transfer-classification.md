# Held-task transfer classification

Replay the actual resend write set in a rolled-back transaction and compare fence refusals to preview eligibility. Include incoming versus outgoing edges, deleted history, malformed/orphan markers and reserved events already fenced; row ownership alone misses related-row refusals.

Keep default selection stable, make skipping explicit with an exact expected set, classify before independent recovery effects, and recheck after every await. Report committed recovery separately from ownership transfer; a later refusal cannot undo an earlier committed phase.

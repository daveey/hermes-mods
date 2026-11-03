# Method limitations

Reference for skill `five-whys`: its SKILL.md links this file from the step that needs it.

## Known weaknesses, and how this procedure covers them

Critics (Teruyuki Minoura at Toyota, Alan J. Card in medicine) point out that five whys:
- **stops at symptoms.** Covered by the stop rule and the never-stop-at list in step 4.
- **can't go past what the investigator already knows.** Covered by evidence-backed answers (step 2): if you
  can't cite evidence, go and get it (logs, repro, instrumentation, `systematic-debugging`) instead of guessing.
- **isn't repeatable: different people get different chains.** Evidence on every step makes the chain checkable.
  For high-stakes postmortems, have a second agent review the chain neutrally ("trace this chain and report which
  steps the evidence supports") instead of asking it to find holes.
- **pushes toward a single root cause.** Covered by branching (step 3) and the separate not-caught chain. For a
  problem with many interacting causes, use a fishbone (Ishikawa) diagram to list candidate causes first, then run a
  why-chain down each branch that survives the evidence.
- **uses an arbitrary depth of five.** Covered by stopping on the step-4 criteria, not on a count.

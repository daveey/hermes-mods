# Board-custody diagnosis

1. Read the target and nominated repair producer. An archived producer cannot deliver a repair; posting evidence there does not create an active handoff.
2. Before delegating custody diagnosis, have the authorized owning context run `hermes kanban --board <board> sync status`. Supply sanitized mode, board identity, gateway and the exact lifecycle refusal to the repair card. Do not equate a private board with an unsynced board: shadow mode can explain a primary/local-only refusal without proving the guard is defective.
3. If a board-pinned repair worker lacks access to this evidence, supply the snapshot through its card from the authorized owning context. Do not ask the worker to bypass isolation with shell, SSH or database reads.
4. Use the evidence to narrow the investigation before resuming it. Do not change sync enrollment or relax custody checks merely to make the operation pass.

This procedure establishes diagnostic evidence, not a working retention operation. Claim a durable hold only after its supported operation returns a receipt and exact-target readback confirms it; a running repair or accepted comment is not that proof.

# BS-stuck-run: reclaiming a card held by a remote lease

- **Read back every reclaim.** On a card another gateway holds, `hermes kanban reclaim <id>` can print
  `Reclaimed` while the Board rejects the write. Read back the status and the newest events. `sync_rejected ...
  live_claim: running under <holder>'s live lease` means the Board still fences the card to that holder, even
  when the local `lease_expires` has passed. Read the local row with
  `~/.hermes/bin/hermes-core-python -m sqlite3 ~/.hermes/kanban.db "select status,claim_lock,lease_holder,lease_expires from tasks where id='<id>'"`
  (the system `sqlite3` is guarded). After that rejection, only the holder's gateway or a Board-side fix can
  release the card. Record the mismatch on the fix card as the root-cause lead.
- **Leave the offline Mac out of the fix card's requires.** When the holder is an offline Mac, don't require
  `ssh:<mac>`: no pool worker carries that label and hub admission refuses the hub, so the card strands with
  `waiting_for_manual_repin`. When the Mac wakes, its own gateway reaps or continues the run. The portable work
  is the lease-fence analysis. Re-scope a stranded card with
  `hermes kanban edit <id> --requires terminal,kanban,hub:read,hub:write`, which an orchestrator can run alone
  (`--requires` can't be combined with other flags). A pool worker then claims it within about a minute.

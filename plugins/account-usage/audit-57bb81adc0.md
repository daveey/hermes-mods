# Re-audit: core 57bb81adc0 + bdf2f36df7 Codex quota benches (fork main 350667116ff9)

Card t_01a1d874f17bda10 (follow-on drift seen during the Mac install probe: the MacBook had already
installed the newer fork main and refused manual picks on `agent/credential_pool.py`).

Drifted pin (old digests kept, new added):
- `execution-compatibility.json` `agent/credential_pool.py`: 9678c60993d4 -> cef74f237d48

What changed in `agent/credential_pool.py` (+18/-19; helpers in the new `agent/credential_pool_bench.py`):
- `mark_exhausted_and_rotate`: when selection hands back the just-marked entry, core used to return `None`
  (#97315 no-self-retry guard). It now re-applies the failure (402/429, or the same key) and selects again
  with `exclude_id=<that entry>`, so it returns a different row or `None`, never the just-marked one.
- `_available_entries` / `_select_unlocked`: new `exclude_id`; candidates come from
  `rotation_candidates(pool, exclude_id)` = `pool._enabled_entries()` minus that id (no new eligibility rule).
- `_resync_stale_entry`: a Codex row with a live quota mark keeps its exhausted status when it adopts
  fresher tokens from auth.json (before: the sync cleared the bench).
- The Codex quota-restored probe now gets `reset_at`/`failure_at`; early bench lifts are logged.

Against selection.py's seams:
- Client factories, `_failed_credential_identity`, `switch_model`, `interruptible_*_api_call`,
  `perform_api_call`: untouched (their files did not move).
- `recovery_wrapper` calls `pool.mark_exhausted_and_rotate(..., credential_id=snapshot.entry_id)` and builds
  the next request-local snapshot from the returned row via `choose_snapshot`. The returned row can no longer
  be the just-marked entry, which only strengthens the wrapper's "one attempt, never a sticky same-account
  retry" rule; `None` still maps to `(False, has_retried_429)`. A different row sharing the marked account's
  identity still meets the marked row in `choose_snapshot`'s `matched` set and is refused as cooling down.
- `choose_snapshot` reads persisted rows and honours `_exhausted_until` / model cooldowns / enabled rows. The
  resync change keeps more Codex benches in the persisted rows, so a manual pick is refused at least as often
  as before. `rotation_candidates` adds no fence beyond `_enabled_entries` (already mirrored by
  `credential_pool_disabled.enabled_entries`).
- New credential/route fence a request-local snapshot could bypass: none.

selection.py unchanged, so `fast-rate-limit-fallback/account-usage-compatibility.json` needs no refresh.
Verified: account-usage tests green against core 350667116ff9; `check_compat.py --core` exit 0.

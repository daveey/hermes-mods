# Inventory CPU acceptance

The inventory budget is **0.1% of one deployment-host core**. There are two distinct
replay checks in `tests/test_bench_inventory.py`:

- Deterministic work regression: historical P1 (0.8%) multiplied by new/old
  decoded bytes **and**, independently, projected runner rows must each be <=0.1%.
  These counters wrap real decoder/projection calls, excluding fixture creation;
  both shapes run everywhere, including ten consecutive fresh-process replays per
  Python (3.11/3.14) in the runners workflow. Substituting the legacy replay as new
  must fail. This bounds the dominant work, not its CPU cost: slower decoders or
  added work outside these operations still require the absolute hardware gate.
  Shared-runner thread-CPU ratios are not an admission assertion.
- Absolute replay: measured inventory thread CPU divided by the **simulated 600s**
  span must be <=0.1%. This runs by default outside GitHub Actions. On GitHub
  Actions (`GITHUB_ACTIONS=true` exactly), it visibly skips because hosted runner
  CPU speed is not deployment CPU speed. Generic `CI=true` does not skip it.

## Mandatory prelanding hardware proof

A green GitHub check alone is **not** absolute acceptance. A trusted operator must
run the following on deployment-class hardware, from the clean exact commit being
reviewed (never run untrusted fork PR code automatically on a private host):

```
python3 plugins/runners/scripts/check_inventory_hardware.py \
  --head <full-40-character-commit> --output <new-directory-outside-checkout>
```

The helper runs Python 3.11 and 3.14, both clustered and scattered refreshes,
1900 runners, 600 simulated seconds, unchanged 5s reads/30s writes. It forces
absolute mode even if GitHub environment variables are inherited, validates both
executed cases (no skips), and records interpreter/module provenance, hardware
identity, raw CPU samples and exact commit. Best of two remains the existing
measurement policy; do not retry failing proofs until green. Diagnose failures.
Timing covers inventory plus snapshot reads, not fixture construction/collector
publication. GC policy remains untouched.

An independent exact-head reviewer must inspect the hardware logs and receipt,
confirm the host is the deployment hardware, and explicitly include the absolute
result in approval before landing. This is a **procedural gate**, not a signed
attestation or an automatic required GitHub check. A new commit requires new proof
and review. Unknown hardware, incomplete logs, failed cases or stale SHA hold
landing. No constant speed factor or the relative ratio substitutes for this proof.

After normal deployment, final acceptance still requires live telemetry over a
matched PID whose age is at least 600s: inventory CPU/wall span <=0.1% of one core.
Replay success is not this production proof. Do not force active backend restarts.

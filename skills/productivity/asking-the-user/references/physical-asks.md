# One physical act, one ask; keep prerequisites current

Before filing a physical, on-device or sign-in `human_request`:

1. Search **all boards and profiles on every relevant gateway**, not only the current board, for open asks
   about the same device and the same evidence. Read their current scope and latest comments. Search device
   aliases too, and distinguish a new capture/run from re-use of the same requested recording. If a board is
   unavailable, record the coverage gap and recover its inventory rather than declaring no duplicate.
2. Reuse the existing owner when the act is the same: comment your exact acceptance requirements there and
   link your work to it (same-board parent; cross-gateway dependency for a remote owner). Do not create a second
   blocked ask, send another action_request, or broaden the owner's acceptance silently. If the existing ask
   has a stale prerequisite, have its owner refresh it; deduplication is not evidence of success.
3. For a new ask put these **separate lines in human_request.scope**, using stable device identifiers and a
   precise evidence/run identifier (not the card id, generic words like “video”, or the whole project):

   ```text
   DEVICE: <device identifier>
   EVIDENCE: <specific act and required capture/session>
   PREREQUISITE: <readable install/file/waiter condition, when applicable>
   EXPIRES: <waiter's actual deadline, when applicable>
   ```

   Preserve identifiers exactly; use the existing owner's labels when reusing its act. Different devices,
   recording sessions, conditions or acceptance evidence are different asks. Never label unrelated acts alike
   merely to suppress a human step. The ask-gate pre-tool guard refuses exact matching pairs on readable local
   boards/profiles with `physical_ask_duplicate` and names the existing owner. It also catches identical legacy
   action+scope pairs, not paraphrases. It does **not** search remote gateways, serialize simultaneous filings,
   or automatically attach/modify cards. The caller must do the cross-gateway search and owner handoff.
4. Re-read every prerequisite now. An installed build already satisfying the required feature version removes
   the install/unlock-to-install ask, **not** the remaining on-device check. Never infer feature inclusion only
   from a larger build number across channels; verify lineage/bundle/device. A file's presence is not proof of
   its content, capture provenance or physical acceptance. Inspect those separately.
5. When the ask names a machine-readable prerequisite, include a read-only `human_request.resolved_check` in
   the target gateway's actual execution context (absolute durable paths, explicit target device/profile).
   Exit 0 when this ask is obsolete/resolved and the owner must resume, 1 while it is still actionable, other
   codes for errors. Core probes before persistence: `ask_already_resolved` means continue without blocking;
   `resolved_check_broken` means repair the probe, not omit it. While blocked core probes again and resumes
   the owner. A resumed card is **not completed** and grants no approval or physical acceptance.
6. For an expiring sign-in/window/waiter, check both successful outcome **and expiry/terminal failure**. Probe
   expiry as “old ask no longer actionable; owner must re-plan”, not “signed in”. On wake re-read the actual
   outcome before advancing: if expired, retire the old supported request/window, establish a fresh supported
   flow and replace the **same card's** ask with the fresh job/tab/deadline and check. A pending user-terminal
   job is reattached by job_id, never duplicated. Do not keep an obsolete login URL or stopped waiter in a
   blocked ask. A constant-success probe or manufactured success receipt is never a substitute.

No rule here authorizes a protected Allow click, sign-in, consent, simulated evidence, auto-approval, or relaxed
acceptance. A genuinely physical step with no machine-readable prerequisite still needs the person. Keep the
remaining physical evidence explicit after prerequisite wake-up; do not close it based on install/file/waiter
state alone.

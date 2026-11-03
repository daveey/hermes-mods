# Clock fixture pitfalls

Reference for skill `five-whys`: its SKILL.md links this file from the step that needs it.

## Pitfalls

- For PR respawn-guard flakes after reassignment, distinguish review-checkpoint authorization from an independent operator handoff. A real `assigned` event strictly later than PR publication intentionally releases the hold, while a same-second tie stays guarded. Pin fixture chronology and test both 0/1-second offsets; assert `resumes_review` rejects the old owner in both, with a positive pre-assignment control. Do not change production ordering or widen retries to enforce an accidentally same-second fixture.
- For receipt-expiry fixture flakes, freeze the tested module's clock before recording scope/publication timestamps, not only at the final expiry assertion. Replace the module's time reference rather than global time.monotonic so test-runner deadlines remain real. Keep real lock/process/file checks and explicitly advance to the inclusive TTL boundary and just beyond it. Prove RED/GREEN with an external controller holding the test process in SIGSTOP past the TTL after publication, then SIGCONT; raising the production TTL or adding retries hides the fixture defect.

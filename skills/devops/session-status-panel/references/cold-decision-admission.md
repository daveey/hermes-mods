# Cold interactive decision admission

Test isolated decision admission through a cold hot-mounted router as well as a warmed one. Concurrent recovery dependencies must await one shielded task per home/event loop, not fill the decision pool with workers waiting on the same recovery lock. Exercise a real decision POST while recovery exceeds the queue deadline; warm-only saturation tests miss this path.

A bounded admission refusal needs an explicit never-started contract and a same-frozen-key Retry path. Generic 503, transport uncertainty and failed receipt GETs must never authorize a repost. Clear a stored retry permission before submitting the identical payload; a lost retry response returns to unknown delivery, not another allowed send. Verify reload persistence and negative cases through the actual renderer.

Failed interactive canonical refreshes must arm the existing bounded decision retry and remain unvouched until recovery, rather than wait silently for an unrelated write. Assert the due time, beat-to-lane wake and successful retry without renewing board/execution clocks.

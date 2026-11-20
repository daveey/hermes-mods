# Kanban hub admission: resolve denies before claiming, defer when screening is unavailable, audit bypasses

Mirror snapshot 5011; ordinals 4384, 4385, 4386, 4387, 4388.

Entries:

- **4384**: fix(kanban): resolve denied hub admission before claiming
- **4385**: fix(kanban): audit bounced hub denies and defer queue absence
- **4386**: Defer hub claims when admission screening is unavailable
- **4387**: Seed historical bypass in doctor census fixture
- **4388**: test(kanban): fence denied-claim disposition races

## Behavior

- Previously any prior verdict (even a deny) skipped judging, a young deny could be claimed after a wait, the exact `machine:<hub>` pin used by a bounced deny was excluded from the screen, and judge outages returned permission. Now unresolved candidates are resolved before claiming: the claim screen evaluates unlabelled legacy cards plus denied cards bounced back to the hub's own machine pin, records a current keep (structural, pickup-constraint or home-coordination) or a move without a new judge call, and does not append duplicate keep events on every screen.
- A deny with no attested queue and no structural keep defers instead of running on the hub; when the screen raises or the judge is unavailable the frozen lanes are deferred to a later tick rather than bypassed.
- The doctor gains a census check that lists hub claims in the last week lacking an admission verdict or carrying a deny without an explicit keep/force, counting audits that ride a `created` payload as well as `hub_admission` events. Creation and claim honor the same pickup constraint so home-coordination cards are recorded as kept at creation.
- Tests fence disposition races (wake race, later correction not repairing history) and seed a historical bypass in the doctor census fixture.

## How it works

- `kanban_hub_admission_claim.py` holds eligibility (`_eligible`), the screen, the keep/move disposition writer and `unjudged_claims`; `kanban_hub_admission_placement.py` shares the pickup constraint; `doctor_state.py` renders the census.
- The dispatcher seam wraps the screen so a failure returns the frozen lane ids as deferred.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/doctor_state.py`
- `hermes_cli/kanban_hub_admission_claim.py`
- `hermes_cli/kanban_hub_admission_placement.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/hermes_cli/test_kanban_claim_deny_selfcheck.py`
- `tests/hermes_cli/test_kanban_denied_claim_disposition.py`
- `tests/hermes_cli/test_kanban_hub_admission_claim.py`
- `tests/hermes_cli/test_kanban_hub_claim_wake_race.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- The census is chronological: a later correction does not repair an earlier bypass in the report.
- Cards pinned explicitly to other machines are outside the screen scope by design.

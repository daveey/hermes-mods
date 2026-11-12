"""Live judge replay: the real JUDGE_SYSTEM prompt against the real pool judge tier, on replies the judge got wrong.

Opt-in (it makes model calls): SESSION_OUTCOME_LIVE_JUDGE=1. Each case runs 3 times; every verdict must match.
The NO cases guard the other side: a prompt that says YES to everything passes the regressions and fails here.
The calls run in a child process (this file run as a script): under pytest, core refuses to read the real auth
store the pool needs. ``python3 tests/test_judge_live.py`` prints every verdict.
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNS = 3

pytestmark = pytest.mark.skipif(os.environ.get("SESSION_OUTCOME_LIVE_JUDGE") != "1",
                                reason="live model calls; set SESSION_OUTCOME_LIVE_JUDGE=1")

# A full answer to "why is it not green?" that names the chat's own cron and its later runs. Before the
# scheduled-later rule the bare YES/NO judge said NO 5/5 (as "a promise to report later").
SCHEDULED_ANSWER = (
    "The icon is correct: this session still has a scheduled job. I created the **spam false-positive audit** cron "
    "(34b6cd8b46f6) from this chat, so it's tied to this session. Its three runs are on Oct 24, Oct 28 and Nov 3 at "
    "10:00 PT, and they post results back here.\n\n"
    "You set the rule on Oct 15: a session with recurring work gets the loop icon instead of green. None of the "
    "cards keep it from green, since all 9 are done. The weekly ask-gate count (dee59d90c7e8) was created by a card, "
    "so it isn't tied to this session.\n\n"
    "The session will turn green on its own after the last audit runs on Nov 3. Archiving it now is fine too: the "
    "audit still runs and still posts to your connected channels.")

# A closing report after a blocked-card notice that names a card queued to run the last check. Before the queued-card
# rule the judge said NO 3/3 ("a queued verification the assistant itself owes").
QUEUED_CHECK_ANSWER = (
    "**The safeguard is not deployed.** It turned out to add nothing, so it was archived as superseded. Nothing needs "
    "you.\n\n"
    "- **Admin page:** fixed and verified earlier. Sign-in works and unauthenticated requests are denied.\n"
    "- **The reply check:** flags answers that stop at a diagnosis. It is already live.\n"
    "- **Why the safeguard was dropped:** on 1,355 real goal stops, 14 ended at a diagnosis. The reply check catches 9; "
    "the safeguard caught none. It is now off (verified as `{\"mode\":\"off\"}`).\n"
    "- **Dispatcher bug** that killed new workers 19 seconds after start: the fix is committed and deployed. Its card is "
    "queued to confirm the fix works on a real card; that check hasn't run yet.")
QUEUED_CHECK_NOTICE = ("[Automatic notice, not typed by the user; it started this turn]\n"
                       "Kanban t_1 blocked: Please close this card as superseded, or tell me to activate the guard "
                       "anyway.")

# A one-line delivery report for a scripted post request. The bare YES/NO judge said NO 3/3 with the full request
# (YES once the request's closing rules were cut) and NO 3/3 with no request; asked to reason first it said YES.
POSTED_REQUEST = (
    "You pressed Post on Bluesky reply draft for https://bsky.app/profile/did:plc:aaa/post/3abc. Post exactly this "
    "text as a reply to at://did:plc:aaa/app.bsky.feed.post/3abc and nothing else, by running in the terminal: "
    "python3 ~/bin/bsky_post_reply.py at://did:plc:aaa/app.bsky.feed.post/3abc <<'BEOF'\nThat makes sense. The "
    "context that explains a deviation mostly lives in people's heads.\nBEOF\n(it runs bsky_engage.py reply under "
    "token-broker, refuses a duplicate, enforces the daily cap and logs to LOG.md). Report the resulting bsky.app "
    "URL, or the error. Do not retry on error and do not post anything else. Never cross-post this to X.")
POSTED_ANSWER = ("I posted the reply to @someone.example.com: https://bsky.app/profile/did:plc:bbb/post/3xyz\n\n"
                 "It went to Bluesky only, not X.")
# A complete review with a verdict (keep/revert) whose last lines note what was deliberately not done. Judged NO.
REVIEW_REQUEST = (
    "Review coach improvement 2026-11-08-1. Show me exactly what changed, then check whether it is helping using "
    "the coach's before/after KPI and recent sessions. Give a one-line verdict: keep or revert. If it is clearly "
    "hurting, run `coach-apply revert 2026-11-08-1 \"<why>\"` and tell me. If I answer keep / not now / never, "
    "record it with `coach-apply decide 2026-11-08-1 keep|not_now|never`.")
REVIEW_ANSWER = (
    "## Exactly what changed\n\nAdded **one bullet under `## Do NOT`** in `skills/devops/kanban-worker/SKILL.md:189`. "
    "Nothing was removed:\n\n> - Change a sibling rollout card's status when its owner explicitly prohibited "
    "transitions.\n\nThe application ledger records November 10, 10:00 AM PST. I verified the live file contains "
    "the bullet once.\n\n## Is it helping?\n\nI ran `coach_weekly.py --check` for this proposal.\n\n"
    "| Measure | Seven days before | After |\n|---|---:|---:|\n| Sessions | 5,595 | 11 |\n"
    "| Repeat-fix signals/session | 0.578 | 0.727 |\n\n**Coach recommendation: KEEP, provisional.** The rule had "
    "been applied for less than an hour; the checker requires at least 15 sessions and three days, so neither "
    "number demonstrates an effect. I inspected all 11 post-application session digests: no clear evidence of "
    "harm, but no causal proof of benefit either.\n\n**Verdict: KEEP provisionally: the rule narrowly preserves "
    "explicit owner instructions, and the post-change evidence is too young to justify reverting.**\n\n"
    "No revert performed, and I have not recorded a user `keep` decision on your behalf.")

CASES = [
    (POSTED_REQUEST, POSTED_ANSWER, "YES"),
    ("", POSTED_ANSWER, "YES"),
    (REVIEW_REQUEST, REVIEW_ANSWER, "YES"),
    ("when tasks are running, show an [H] in their box if they run on the hub",
     "Cards scheduled for the hub will get an **H** in their colored box. It isn't showing yet: the frontend part "
     "is deployed, but the backend part is still going through CI, and I haven't seen it on screen.", "NO"),
    ("why is it not green? it has a schedule icon", SCHEDULED_ANSWER, "YES"),
    (QUEUED_CHECK_NOTICE, QUEUED_CHECK_ANSWER, "YES"),
    ("why is the chat still yellow?",
     "It's yellow because card t_123 is still running the soak test, which ends Friday. The chat turns green by "
     "itself when that card finishes; nothing needs you.", "YES"),
    ("set up the nightly backup",
     "Done. The nightly backup cron (a1b2c3) runs at 02:00 PT and posts each result here. The first run is tonight; "
     "nothing else needs you.", "YES"),
    ("is the deploy done?",
     "Started the deploy. CI is running now; I'll report back here when it finishes.", "NO"),
    ("fix the flaky test",
     "I couldn't fix it yet: the failure doesn't reproduce locally. A cron will rerun the suite tomorrow at 09:00 "
     "and I'll look at the result then.", "NO"),
    ("ship the release",
     "The release PR is open and CI is running; the card will merge it on its own when checks pass, and I'll post "
     "the release link here.", "NO"),
    ("why is the dashboard red?",
     "It's red because the nightly import failed: the S3 key expired. I haven't fixed it yet.", "NO"),
    ("migrate the db",
     "The migration is ready but needs your approval: reply yes to run it against prod.", "NO"),
    ("set up the nightly backup",
     "Which bucket should the backups go to: the prod one or the archive one?", "NO"),
    # kanban-310ed068f0: an unjudged `local` chat went green on this reply through core's length heuristic; the
    # judge itself said NO 8/8 on the original.
    ("is the eval suite built yet?",
     "No. The requested suite is not built yet. Its hub card is still queued and nobody has claimed it, so nothing "
     "has run.", "NO"),
]


def _load():
    spec = importlib.util.spec_from_file_location("session_outcome_live_judge", ROOT / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _verdicts() -> list[list[str]]:
    mod = _load()
    def one(request, message):
        t0 = time.monotonic()
        raw = mod._llm(mod.JUDGE_SYSTEM, mod._judge_input(message, request))
        verdict = mod._verdict_of(raw)
        print(f"{time.monotonic() - t0:.1f}s", file=sys.stderr)
        return "YES" if verdict is True else "NO" if verdict is False else f"?{str(raw)[-60:]}"
    return [[one(request, message) for _ in range(RUNS)] for request, message, _ in CASES]


@pytest.fixture(scope="module")
def verdicts():
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)  # the child sees core and pytest as we do
    out = subprocess.run([sys.executable, __file__], env=env, capture_output=True, text=True, timeout=600)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("i", range(len(CASES)), ids=[f"{c[2]}-{i}" for i, c in enumerate(CASES)])
def test_live_judge(verdicts, i):
    expected = CASES[i][2]
    assert all(v.startswith(expected) for v in verdicts[i]), (CASES[i][1][:80], verdicts[i])


if __name__ == "__main__":
    print(json.dumps(_verdicts()))

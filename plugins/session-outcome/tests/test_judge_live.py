"""Live judge replay: the real JUDGE_SYSTEM prompt against the real pool fast tier, on replies the judge got wrong.

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

CASES = [
    ("why is it not green? it has a schedule icon", SCHEDULED_ANSWER, "YES"),
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
]


def _load():
    spec = importlib.util.spec_from_file_location("session_outcome_live_judge", ROOT / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _verdicts() -> list[list[str]]:
    mod = _load()
    return [[mod._llm(mod.JUDGE_SYSTEM, mod._judge_input(message, request)).strip().strip(".").upper()
             for _ in range(RUNS)] for request, message, _ in CASES]


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

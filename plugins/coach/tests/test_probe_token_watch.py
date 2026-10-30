"""probe_token_watch: a person's chat whose newest turns are an agent's "Reply exactly <TOKEN>" probe.

Regression for self-heal agent-93a55211b1: a fix worker sent `hermes chat --resume <id> --oneshot -q "... Reply
exactly ROUTE_INSTALLED_23ef49"` into David's Desktop chat 23ef49 and his phone-calls chat 55b17f; the coach digest
showed last_reply = ROUTE_INSTALLED_23ef49 and nothing fired.
"""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "probe_token_watch.py"
spec = importlib.util.spec_from_file_location("probe_token_watch", PATH)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def digest(sid, source="desktop", last_reply="Done.", texts=(), t=1793318618):
    return {"id": sid, "source": source, "title": "t", "host": "macbook", "profile": "default",
            "last_activity_at": t, "last_reply": last_reply,
            "user_turns": [{"t": t - 100 + i, "text": x, "prev": ""} for i, x in enumerate(texts)]}


INCIDENT = digest("20261026_000514_23ef49", last_reply="ROUTE_INSTALLED_23ef49", texts=(
    "i want alpha in slack",
    "Routing acceptance check only for t_01a189b19f7a10d2. Do not call tools. Reply exactly ROUTE_ACCEPTED_23ef49.",
    "Routing installed-code acceptance only. Do not run tools or resume old work. Reply exactly "
    "ROUTE_INSTALLED_23ef49."))


class FakeRun:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, input=None, **kw):
        self.calls.append((argv, input))
        out = json.dumps({"id": f"t_{len(self.calls):04d}"}) if "create" in argv else ""
        return subprocess.CompletedProcess(argv, 0, out, "")


class WatchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.home = self.root / "home"
        (self.data / "macbook" / "default").mkdir(parents=True)
        self.home.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def put(self, dg, host="macbook"):
        d = self.data / host / dg.get("profile", "default")
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{dg['id']}.json").write_text(json.dumps(dg))

    def test_incident_digest_is_flagged(self):
        hit = w.check(INCIDENT)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["session"], "20261026_000514_23ef49")
        self.assertIn("ROUTE_INSTALLED_23ef49", hit["evidence"])

    def test_token_last_reply_alone_is_flagged(self):
        self.assertIsNotNone(w.check(digest("s1", source="cli", last_reply="  ROUTE_ACCEPTED_55b17f\n",
                                            texts=("call mom at 5",))))

    def test_not_flagged(self):
        for dg in (
            digest("s2"),  # ordinary chat
            digest("s3", source="kanban", last_reply="OK_TOKEN_1", texts=("Reply exactly OK_TOKEN_1",)),
            digest("s4", source="oneshot", last_reply="PONG", texts=("Reply exactly PONG",)),
            digest("s5", last_reply="Yes."),  # short ordinary answer, not a TOKEN
            digest("s6", last_reply="OK"),  # a plain word is not a probe token
            digest("s7", texts=("how do I make it reply exactly the same thing twice?",)),
            # an agent's own throwaway Desktop test session: the first user turn IS the probe
            digest("s8", last_reply="PAUSE_PROBE_DONE", texts=(
                "Authorized throwaway probe. Run sleep 45, then reply exactly PAUSE_PROBE_DONE.",)),
            digest("s9", texts=("Isolated transport test. Do not use tools. Reply exactly: READY",
                                "Do not use tools. Reply exactly: SM-IDLE-1e224dce")),
            digest("s10", last_reply="ROUTE_X_1"),  # bare token but no person ever typed here
        ):
            with self.subTest(sid=dg["id"]):
                self.assertIsNone(w.check(dg))

    def test_scan_files_one_card_per_session_then_comments_only_on_new_evidence(self):
        self.put(INCIDENT)
        self.put(digest("20261025_073850_55b17f", source="desktop", last_reply="ROUTE_INSTALLED_55b17f",
                        texts=("can BB answer calls?", "Reply exactly ROUTE_INSTALLED_55b17f",)))
        self.put(digest("20261030_000000_normal"))
        run = FakeRun()
        self.assertEqual(w.main(["--data", str(self.data), "--home", str(self.home), "--no-pull", "--json"],
                                runner=run), 0)
        creates = [c for c in run.calls if "create" in c[0]]
        self.assertEqual(len(creates), 2)
        keys = sorted(c[0][c[0].index("--idempotency-key") + 1] for c in creates)
        self.assertEqual(keys, ["probe-token:macbook:20261025_073850_55b17f",
                                "probe-token:macbook:20261026_000514_23ef49"])
        self.assertIn("hermes sessions branch", creates[0][1])
        # second run, same digests: nothing new
        run2 = FakeRun()
        w.main(["--data", str(self.data), "--home", str(self.home), "--no-pull"], runner=run2)
        self.assertEqual(run2.calls, [])
        # a newer probe in the same chat -> comment on the existing card, not a new card
        newer = dict(INCIDENT, last_activity_at=INCIDENT["last_activity_at"] + 600,
                     last_reply="ROUTE_AGAIN_23ef49")
        self.put(newer)
        run3 = FakeRun()
        w.main(["--data", str(self.data), "--home", str(self.home), "--no-pull"], runner=run3)
        self.assertEqual([c[0][:3] for c in run3.calls], [["hermes", "kanban", "comment"]])

    def test_dry_run_files_nothing(self):
        self.put(INCIDENT)
        run = FakeRun()
        w.main(["--data", str(self.data), "--home", str(self.home), "--no-pull", "--dry-run"], runner=run)
        self.assertEqual(run.calls, [])


if __name__ == "__main__":
    unittest.main()

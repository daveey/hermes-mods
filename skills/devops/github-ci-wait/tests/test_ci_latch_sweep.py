"""ci_latch_sweep cancels only runs latched on a gate job that is still waiting for a runner."""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ci_latch_sweep as s  # noqa: E402

GATE = re.compile(s.DEFAULT_GATE, re.I)
RUN = {"status": "in_progress", "run_attempt": 1, "head_sha": "old"}


def job(name, status="completed"):
    return {"name": name, "status": status}


class LatchedTests(unittest.TestCase):
    def test_superseded_run_with_queued_gate_is_latched(self):
        jobs = [job("Python tests"), job("All required checks pass (v2)", "queued")]
        self.assertTrue(s.latched(RUN, jobs, "new", GATE))

    def test_gate_already_running_on_a_runner_is_left_to_finish(self):
        # A gate on a light lane finishes in seconds; cancelling it turns an all-green run red.
        jobs = [job("Python tests"), job("All required checks pass (v2)", "in_progress")]
        self.assertFalse(s.latched(RUN, jobs, "new", GATE))

    def test_open_real_job_keeps_the_run(self):
        jobs = [job("Python tests", "in_progress"), job("All required checks pass (v2)", "queued")]
        self.assertFalse(s.latched(RUN, jobs, "new", GATE))

    def test_current_head_and_reruns_are_kept(self):
        jobs = [job("All required checks pass (v2)", "queued")]
        self.assertFalse(s.latched(RUN, jobs, "old", GATE))
        self.assertFalse(s.latched({**RUN, "run_attempt": 2}, jobs, "new", GATE))


if __name__ == "__main__":
    unittest.main()

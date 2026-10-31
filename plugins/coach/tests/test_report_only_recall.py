"""report_only recall on human turns: owner claims without a check and small/optional leftovers reach the judge, and
the screen prompt says how to score them. Synthetic paraphrases only; private replay data must never enter this repo."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("ro_recall_rc", Path(__file__).resolve().parents[1] / "reply_check.py")
assert spec is not None and spec.loader is not None
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

OWNER_CLAIMS = (
    "The upload works now. What's left is the speed; card t_0000aaaa is working on that now.",
    "Still to do: confirm the camera is live and end the test events. The card is doing these now.",
    "One blocked issue remains (disk space); the Steward is handling it.",
    "Handoff complete. The other session owns remediation and the final report.",
    "It's grouped with two older issues, and fix work will start once that hold is lifted.",
    "Everything else is finished; the gallery test is waiting until your laptop is less busy.",
)
SMALL_LEFTOVERS = (
    "The room launcher still starts from a terminal. It works fine; moving it over is optional tidy-up.",
    "The summary still caps its count at 1,000 results. I left that alone because it never drops an item.",
    "Files are in the design folder, plus a leftover test screenshot.",
    "Three devices answered one question. Fixing it means making sure only one device answers each wake.",
    "The plugin changes require a gateway restart to take effect.",
    "Approved, but not posted: the helper rejected the wording.",
    "This sets up the pipeline. The next question is whether the search is missing solutions.",
)
CLEAN = (
    "Merged and deployed; the dashboard shows the new build.",
    "Here is the summary you asked for: three sections, each with sources.",
)


def test_owner_claims_without_a_check_are_candidates():
    for t in OWNER_CLAIMS:
        assert "report_only" in rc.prefilter(t), t


def test_small_or_optional_leftovers_are_candidates():
    for t in SMALL_LEFTOVERS:
        assert "report_only" in rc.prefilter(t), t


def test_clean_replies_are_not_candidates():
    for t in CLEAN:
        assert "report_only" not in rc.prefilter(t), t


def test_screen_prompt_scores_owner_claims_and_small_leftovers():
    rule = rc.REPORT_ONLY_RULE  # the screen's (JUDGE_SYS) report_only rubric
    assert "Owner claims need a check" in rule and "A message SENT to an owner is not a check" in rule
    assert "Small or optional leftovers still count" in rule and "optional tidy-up" in rule
    assert "report_only must be at least 0.7" in rule
    assert rule in rc.judge_system()

"""restart_scope_claim (self-heal agent-8812b31b28): a Desktop reply claiming an install restarts or refreshes this
chat's backend, or that a fix is "live in the Desktop backend", while this backend still runs its boot revision
(gateway.code_skew), is held deterministically before the judge. On a Mac the updater never restarts the Desktop's own
serve backend (core update_inventory CLIENT_OWNED_SERVE_SUPERVISORS -> "deferred"); the backend retires itself only
once every chat on it is idle (web_server_skew_exit), so until then this chat keeps the old code. Keyed on the skew
state: the same words with no skew pass."""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t_reply_check_restart_scope", HERE / "reply_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

SKEW = ("a1b2c3d4e5", "f6e7d8c9b0")

# The 2026-11-11 MacBook Desktop chat's wording (coach digest) plus the claim it later retracted.
LIVE_CLAIM = ("Pushed and installed. It's live in the Desktop backend, and the gateway picks it up when it next "
              "restarts.")
RESTART_CLAIM = "The install will restart this chat's Desktop backend, so the fix is active here after that."


def _no_judge(system, prompt):
    raise AssertionError("restart_scope_claim must not need the LLM judge")


def test_live_claim_under_skew_is_held_before_the_judge():
    hit = rc.check(LIVE_CLAIM, "ship the fix", ["-> terminal(command='hermes update --yes')"], _no_judge,
                   only=["restart_scope_claim"], platform="desktop", code_skew=lambda: SKEW)
    assert hit and hit["rule"] == "restart_scope_claim" and hit["p"] == 1.0
    assert "live in the Desktop backend" in hit["evidence"]
    assert "a1b2c3d4e5" in hit["nudge"] and "idle" in hit["nudge"]


def test_restart_claim_under_skew_is_held():
    hit = rc.check(RESTART_CLAIM, "ship it", [], _no_judge, only=["restart_scope_claim"], platform="desktop",
                   code_skew=lambda: SKEW)
    assert hit and hit["rule"] == "restart_scope_claim"
    for text in ("Once the update finishes, `hermes update` restarts the Desktop backend and this chat picks it up.",
                 "The core update refreshes this chat's backend, so you'll see it on your next message.",
                 "After the install this session reloads on the new code."):
        assert rc.restart_scope_claim(text, skew=lambda: SKEW), text


def test_no_skew_stays_silent():
    assert rc.restart_scope_claim(LIVE_CLAIM, skew=lambda: None) is None
    assert rc.check(LIVE_CLAIM, "ship it", [], _no_judge, only=["restart_scope_claim"], platform="desktop",
                    code_skew=lambda: None) is None


def test_skew_read_only_when_the_wording_matches():
    calls = []
    assert rc.restart_scope_claim("Tests pass and the PR is merged.", skew=lambda: calls.append(1) or SKEW) is None
    assert calls == []


def test_unreadable_skew_fails_open():
    def boom():
        raise RuntimeError("no core")
    assert rc.restart_scope_claim(LIVE_CLAIM, skew=boom) is None


def test_correct_rule_and_quotes_pass():
    for text in ("The install doesn't restart this chat's Desktop backend; it refreshes once every chat is idle.",
                 "This backend won't pick it up until it goes idle and Desktop respawns it.",
                 "You said \"it's live in the Desktop backend\" earlier; that was wrong.",
                 "> It's live in the Desktop backend.",
                 "The gateway picks it up when it next restarts.",
                 "Restart the Desktop backend with the Models page button if you want it now."):
        assert rc.restart_scope_claim(text, skew=lambda: SKEW) is None, text


def test_non_desktop_platforms_are_not_held():
    for platform in ("kanban", "telegram", "cron"):
        assert rc.check(LIVE_CLAIM, "ship it", [], _no_judge, only=["restart_scope_claim"], platform=platform,
                        code_skew=lambda: SKEW) is None, platform


def test_rule_is_on_by_default():
    assert "restart_scope_claim" in rc.RULES and "restart_scope_claim" in rc.DEFAULT_RULES
    assert "restart_scope_claim" in rc.settings()["rules"]

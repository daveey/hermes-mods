"""Hermes-written plugin notices are never judged as David's complaints (agent-b3f86b6bb7).

The backstop filed "[ask-gate superseding_work] Your reply says ..." as David's complaint because SYNTHETIC_RE only
knew capitalised tags plus a hand-kept list ([chat-closeout], [coach]). Lowercase plugin tags ([ask-gate],
[owner-wake], [kanban], [runtime]) leaked one at a time. The fix derives the tag set from the plugin directories,
and the guard below fails CI when a plugin writes a new bracket-tag notice the classifier does not recognise.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sh = load("coach_self_heal_synthetic_tags_test", ROOT / "self_heal.py")
core = load("coach_core_synthetic_tags_test", ROOT / "core.py")

# Real evidence strings from coach/log.jsonl selfheal_detect rows (source=backstop/complaint).
REAL_NOTICES = [
    "[ask-gate superseding_work] Your reply says there is no sign of a change the user named, but these open or "
    "recently decided cards touch it",
    "[ask-gate, private: David has NOT seen your draft.] Your draft asked David for something that fails the "
    "pre-send check",
    "[owner-wake] Card(s) this chat owns parked for a reason only an agent can clear; nothing else will produce "
    "what they wait on",
    "[kanban] Task t_01a18c02e0a94004 blocked; needs attention.\nTitle: Disk H5: mini hit 15 GB",
    "[runtime] Your draft above was NOT shown to the user. Your next reply replaces it: include its full content",
    "[kanban-honest-done] The card you completed still has failing acceptance",
    "[workflow-state] The workflow is still blocked; a card entering review is not recovery",
]


@pytest.mark.parametrize("text", REAL_NOTICES)
def test_plugin_notice_is_not_judged(text):
    assert sh.SYNTHETIC_RE.match(text), text
    assert sh.prefilter(text) is False


@pytest.mark.parametrize("text", [
    "[screenshot] still broken",
    "[selfheal-probe] run x three times",
    "why is the kanban board still showing the old card? you said it was fixed",
    "[asked] this is still broken",          # an unknown lowercase tag is still the user's words
    "[kanbanish] nope",                       # a tag only counts when it is a whole Hermes tag
])
def test_user_text_still_judged(text):
    assert not sh.SYNTHETIC_RE.match(text), text
    assert sh.prefilter(text) is True


def test_probe_still_reaches_probe_path():
    assert sh.PROBE_RE.match("[selfheal-probe] run x")
    assert "selfheal-probe" not in sh.HERMES_TAGS


def test_queued_backstop_row_with_plugin_quote_is_withdrawn(tmp_path):
    """Rows queued before the classifier knew a tag are withdrawn when the lane next handles them."""
    store = core.Store(tmp_path / "coach")
    d = {"fingerprint": "agent-b3f86b6bb7", "component": "agent", "symptom": "omitted relevant ongoing work",
         "source": "backstop", "session_id": "s1", "quote": REAL_NOTICES[0], "host": "h", "at": 1.0,
         "p_defect": 0.8}
    res = sh.file_defect(d, store=store, m="live", cfg={})
    assert res == {"action": "skip", "why": "synthetic_tag"}
    rows = [json.loads(ln) for ln in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]
    assert any(r.get("event") == "selfheal_withdrawn" and r.get("why") == "synthetic_tag"
               and r.get("fingerprint") == "agent-b3f86b6bb7" for r in rows)


def test_pending_synthetic_row_is_withdrawn_even_behind_a_cap_denial(tmp_path):
    """Hub, 11-09: [runtime] row agent-820df70fab sat 3.5 days overdue because every retry pass stopped at the first
    cap denial before reaching it. Withdrawal runs in the pre-pass, so the cap never shields a synthetic row."""
    import sqlite3
    store = core.Store(tmp_path / "coach")
    db = sh._pending_db(store)
    real = {"fingerprint": "agent-real", "source": "backstop", "quote": "it is still broken", "at": 99.0,
            "p_defect": 0.9}
    fake = {"fingerprint": "agent-820df70fab", "source": "backstop", "at": 99.0, "p_defect": 0.5,
            "quote": "[runtime] Your draft above was NOT shown to the user. Your next reply replaces it"}
    with db:
        for d in (real, fake):
            db.execute("INSERT INTO pending VALUES (?, ?, ?, ?)", (d["fingerprint"], json.dumps(d), 1.0, "v1"))
    db.close()
    filed = []

    def deny(d, **k):
        filed.append(d["fingerprint"])
        return {"action": "deferred", "why": "self-heal concurrency limit reached"}

    out = sh.retry_deferred(store, now=100.0, filer=deny)
    assert {"action": "skip", "why": "synthetic_tag"} in out
    assert filed == ["agent-real"]  # the synthetic row never reached the filer
    left = [r[0] for r in sqlite3.connect(tmp_path / "coach" / "selfheal_pending.sqlite").execute(
        "SELECT fp FROM pending")]
    assert left == ["agent-real"]
    rows = [json.loads(ln) for ln in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]
    assert any(r.get("event") == "selfheal_withdrawn" and r.get("fingerprint") == "agent-820df70fab" for r in rows)


def test_machine_signal_quote_is_not_withdrawn():
    """Only user-text sources are withdrawn: a cron/kanban signal quoting a tagged log line is still a defect."""
    assert sh.synthetic_user_row({"source": "cron", "quote": "[kanban] dispatcher crashed"}) is False
    assert sh.synthetic_user_row({"source": "backstop", "quote": "[kanban] Task t_1 blocked"}) is True
    assert sh.synthetic_user_row({"source": "complaint", "quote": "[owner-wake] Card(s) ..."}) is True
    assert sh.synthetic_user_row({"source": "backstop", "quote": "it is still broken"}) is False


# ── Detection guard: every plugin's bracket-tag notice prefix counts as Hermes-written ──
# A literal that starts a user-role string with "[<lowercase-tag>" followed by "]", space, ":" or ",".
# Raw strings are regexes here, and a tag needs a 2+ char first segment, so char classes ("[a-z0-9]") never count.
NOTICE_LIT = re.compile(r"""(?<![\w\\])[fF]?["']\[([a-z][a-z0-9]+(?:-[a-z0-9]{2,})*)(?=[\] :,])""")
# Bracket literals in plugin source that are not user-role notices (log prefixes, placeholders, markers).
NOT_NOTICES = {
    "pid": "coach/signals.py: crash-log line prefix, never a user turn",
    "bot": "ship-only-guard/guard.py: GitHub login suffix (\"name[bot]\") in a reviewer-author check, never a user turn",
    "older": "coach/reply_check.py: '[older tool calls omitted]' placeholder inside a tool log",
    "redacted": "tailnet/runner.py: redaction placeholder in command output",
    "hermes": "coach/backend_timeouts.py: parses a '[hermes] ' log prefix",
    "slow-link": "coach/backend_timeouts.py: parses the Desktop's '[slow-link] ...' desktop.log lines",
    "ssh": "coach/backend_timeouts.py: parses the Desktop's '[ssh] forwarding/teardown ...' desktop.log lines",
    "unparseable-query-dropped": "loop-watch/reqtrace.py: placeholder in a request trace",
    "selfheal-probe": "PROBE_RE: probes must reach the probe path (selfheal_skip why=probe), not be dropped",
    "message": "coach/signals.py: lowercased match key for the cross-session relay header (CROSS_SESSION_RE)",
    "on": "quiet-mode/__init__.py: slash-command args_hint \"[on [2h]|off|status]\"",
    "image": "placeholder for an attached image in rendered transcripts",
    "screenshot": "clean_user: stands in for David's own screenshot, must still be judged",
}


def _plugin_sources():
    for p in sorted(PLUGINS.rglob("*")):
        if p.suffix not in (".py", ".js", ".mjs") or not p.is_file():
            continue
        rel = p.relative_to(PLUGINS).parts
        if any(part in ("tests", "node_modules", "__pycache__") or part.startswith("test") for part in rel):
            continue
        if p.name.startswith("test") or p.name.startswith("tests_"):
            continue
        yield p


def test_every_plugin_notice_tag_is_synthetic():
    found = {}
    for p in _plugin_sources():
        for m in NOTICE_LIT.finditer(p.read_text(errors="replace")):
            found.setdefault(m.group(1), str(p.relative_to(PLUGINS)))
    assert {"ask-gate", "owner-wake", "chat-closeout", "coach"} <= set(found), found
    leaked = {tag: src for tag, src in found.items()
              if tag not in NOT_NOTICES and not sh.SYNTHETIC_RE.match(f"[{tag}] notice text")}
    assert not leaked, ("plugin notice tags the self-heal backstop would judge as David's words; name the plugin "
                        f"dir after the tag, add it to self_heal.EXTRA_HERMES_TAGS, or list it in NOT_NOTICES: {leaked}")


def test_core_notice_tags_are_synthetic():
    """Hermes core (not in this repo) writes [kanban] notifier notices and [runtime] pre-final-gate notices."""
    for tag in ("kanban", "runtime", "owner-wake", "coach", "chat-closeout"):
        assert sh.SYNTHETIC_RE.match(f"[{tag}] notice"), tag


# ── agent-71a1e1fdca: a held-draft notice row filed BEFORE the classifier fix must not become a fix card ──
# Hub message 935839 (session 20261031_064316_d54356): core's held_nudge() row, "[runtime] ... NOT shown" + the
# coach nudge, was filed as David's complaint on 11-09 (before f8d40bd5 taught SYNTHETIC_RE the [runtime] tag), then
# proposed, then auto-approved 17 h later: pending/file re-checks withdrew its siblings, but fix admission never did.
HELD_ROW = ("[runtime] Your draft above was NOT shown to the user. Your next reply replaces it: include its full "
            "content; don't refer to it as 'above'.\n\n[coach] Your message ends on a problem you could fix (offline "
            "placeholder rendering still needs the existing client acceptance checks.)")
# Pinned copy of core's agent/pre_final_gate.HELD_NOTICE; test_held_notice_contract compares it when core is present.
PINNED_HELD_NOTICE = ("[runtime] Your draft above was NOT shown to the user. Your next reply replaces it: include its "
                      "full content; don't refer to it as 'above'.")


def _core_held_nudge():
    try:
        from agent.pre_final_gate import HELD_NOTICE, held_nudge  # type: ignore
    except Exception:
        return None, None
    return HELD_NOTICE, held_nudge


def test_held_notice_contract():
    """Built from core's real held_nudge() output (or the pinned notice when core is absent): every coach classifier
    treats the row as Hermes-written. Core rewording the notice turns the pinned comparison red."""
    notice, held_nudge = _core_held_nudge()
    if notice is not None:
        assert notice == PINNED_HELD_NOTICE, "core HELD_NOTICE changed: update PINNED_HELD_NOTICE and re-check"
        row = held_nudge("[coach] The reply is still not fixed; rerun the acceptance check.", shown=False)
    else:
        row = f"{PINNED_HELD_NOTICE}\n\n[coach] The reply is still not fixed; rerun the acceptance check."
    assert sh.prefilter(row) is False
    assert sh.synthetic_user_row({"source": "backstop", "quote": row}) is True
    # core.py: the still-broken counter and the curriculum must skip it too (core.py:75 had only capitalised tags).
    assert core.is_still_broken(row) is False
    assert core.SYNTHETIC_RE.match(row)


def test_core_skips_coach_nudge_rows_but_not_user_text():
    assert core.is_still_broken("[coach] still not working: rerun it") is False
    assert core.is_still_broken("[ui] the button is still broken") is True
    assert core.is_still_broken("it is still broken") is True
    assert sh.prefilter("[ui] the button is broken") is True


def test_fix_admission_withdraws_a_proposed_synthetic_row(tmp_path):
    """A defect whose detection quote is Hermes-written (by today's classifier) never gets a fix card."""
    store = core.Store(tmp_path / "coach")
    fp = "agent-71a1e1fdca"
    store.log({"event": "selfheal_detect", "mode": "live", "session_id": "20261031_064316_d54356",
               "source": "backstop", "fingerprint": fp, "evidence": HELD_ROW})
    sh.LaneState(store.root).update(fp, status="proposed", component="agent", card="t_inv",
                                    symptom="Left actionable acceptance checks without follow-up")
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        raise AssertionError(f"no command may run: {argv[:4]}")

    ok = lambda board=None, now=None: {"usd": 1.0, "cap": 25.0, "running": 0, "ok_usd": True,
                                         "ok_running": True, "why": ""}
    res = sh.file_fix(fp, store=store, runner=runner, now=1000.0, budget_fn=ok, trigger="auto")
    assert res["action"] == "skip" and res["why"] == "synthetic_tag", res
    assert not calls
    assert sh.LaneState(store.root).get(fp)["status"] == "not_defect"
    rows = [json.loads(ln) for ln in (tmp_path / "coach" / "log.jsonl").read_text().splitlines()]
    assert any(r.get("event") == "selfheal_withdrawn" and r.get("fingerprint") == fp
               and r.get("stage") == "fix" for r in rows)


def test_fix_admission_keeps_a_real_complaint(tmp_path):
    store = core.Store(tmp_path / "coach")
    fp = "agent-realcompl1"
    store.log({"event": "selfheal_detect", "source": "backstop", "fingerprint": fp,
               "evidence": "it is still broken after your fix"})
    sh.LaneState(store.root).update(fp, status="proposed", component="agent", card="t_inv", symptom="x")
    assert sh._synthetic_fix_row(store, fp) is False

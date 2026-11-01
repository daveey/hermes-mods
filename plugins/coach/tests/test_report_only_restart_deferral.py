"""A final reply that leaves a fix dead "until the next restart" gets the report_only second opinion whatever the screen
scored it, unless the turn restarted the backend or filed a card (self-heal agent-a3c36a6f70).

The 2026-10-29 incident reply below scored 0.72 on the screen, under the 0.75 floor, so the second opinion never
ran and the dashboard served the old plugin_api.py for ~11 h. Desktop human turns now use a 0.60 floor; Kanban
worker, CLI, TUI, subagent and notice turns still use 0.75, so the same ending there was still skipped.
"""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('restart_deferral_rc', Path(__file__).resolve().parents[1] / 'reply_check.py')
assert spec is not None and spec.loader is not None
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

HUMAN_ASK = 'The File it once button returns a 500. Fix it.'
NOTICE_ASK = '[chat-closeout] Workers settled. Review the File it once 500.'
# The incident reply's ending, verbatim, after a shortened first paragraph.
REPLY = ("**The 500 error after the banner text.** That's the \"File it once\" button failing. I changed it to use the "
         "`hermes` board when one exists and the current board otherwise. I added a test for that case and the coach "
         "test file passes (15 tests). The fix is pushed to hermes-mods as `0803ca0`.\n\n"
         "The running dashboard reloads Coach's `core.py` live but not the endpoint file I changed, so the button "
         "keeps returning the 500 until the dashboard's next restart.")
SCREEN_P = 0.72  # the incident's logged screen score (report_only_scored, floor 0.75)


def _llm(p):
    return lambda system, prompt: json.dumps({'handoff': 0, 'confirm': 0, 'unverified': 0, 'unwatched': 0,
                                              'report_only': p, 'evidence': 'screen'})


def _run(reply, ask, tool_log=(), human_floor=None, p=SCREEN_P):
    asked, audits = [], []
    hit = rc.check(reply, ask, list(tool_log), _llm(p), only=['report_only'],
                   thresholds={'report_only': 0.75, 'report_only_screen': 0.85},
                   confirm=lambda: asked.append(1) or (0.9, 'until the dashboard next restart'),
                   confirm_floor=rc.CONFIRM_FLOOR, human_confirm_floor=human_floor, audit=audits.append)
    return hit, asked, audits


def test_incident_reply_is_a_report_only_candidate():
    assert 'report_only' in rc.prefilter(REPLY)


def test_incident_reply_gets_the_second_opinion_below_the_floor_on_every_platform():
    for ask, floor in ((HUMAN_ASK, None), (NOTICE_ASK, None), (NOTICE_ASK, rc.HUMAN_CONFIRM_FLOOR)):
        hit, asked, audits = _run(REPLY, ask, human_floor=floor)
        assert asked, (ask, floor)
        assert hit and hit['rule'] == 'report_only' and hit['p'] == 0.9
        scored = [a for a in audits if a.get('event') == 'report_only_scored'][-1]
        assert scored['confirm_attempted'] is True and scored.get('forced') == 'restart_deferral'


def test_a_turn_that_restarted_the_backend_or_filed_a_card_keeps_the_floor():
    for line in ('-> terminal({"command": "hermes-sync pause-turns -- systemctl --user restart '
                 'hermes-always-on-dashboard"})',
                 '-> terminal({"command": "launchctl kickstart -k gui/501/com.hermes.always-on-dashboard"})',
                 '-> kanban_create({"title": "restart the dashboard onto 0803ca0"})',
                 '-> tool_call({"calls": [{"name": "kanban_create", "arguments": {"title": "x"}}]})'):
        hit, asked, _ = _run(REPLY, HUMAN_ASK, tool_log=[line])
        assert not asked and hit is None, line


def test_a_failed_restart_still_forces_the_second_opinion():
    log = ['-> terminal({"command": "systemctl --user restart hermes-always-on-dashboard"})',
           '<- ERROR: Failed to restart hermes-always-on-dashboard.service: Access denied']
    hit, asked, _ = _run(REPLY, HUMAN_ASK, tool_log=log)
    assert asked and hit


def test_reply_without_a_restart_deferral_keeps_the_floor():
    reply = 'The export fails because the temp dir fills up. The cleanup is not fixed.'
    hit, asked, _ = _run(reply, HUMAN_ASK)
    assert not asked and hit is None


def test_phrase_variants():
    for t in ('It stays broken until the next restart.', 'The new code loads until the gateway restarts.',
              'The button 500s until the dashboard is restarted.', 'Old routes are served until its next restart.',
              'It keeps the stale module until a restart.', 'Live after the next restart of the backend.',
              'It takes effect on the next restart.'):
        assert rc.restart_deferral(t, []), t
    for t in ('I restarted the dashboard and the button returns 200.',
              'The unit restarted at 18:12, after the push.',
              'Sync restarts it within 10 minutes; I confirmed the restart line in the log.'):
        assert not rc.restart_deferral(t, []), t


def _hooks(tmp_path, monkeypatch, name, screen_p):
    monkeypatch.delenv('COACH_REPLY_RULES', raising=False)
    monkeypatch.delenv('COACH_REPORT_ONLY_CONFIRM_CAP', raising=False)
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / '__init__.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    core = mod._load('core')
    rcl = mod._load('reply_check')
    monkeypatch.setattr(rcl, 'aux_judge', lambda s, p: json.dumps(
        {'handoff': 0, 'confirm': 0, 'unverified': 0, 'unwatched': 0, 'report_only': screen_p, 'evidence': 'screen'}))
    confirms = []
    monkeypatch.setattr(rcl, 'aux_confirm', lambda s, p: confirms.append(p) or json.dumps(
        {'outcome': 'report_only', 'agent_fixable': True, 'quote': 'until the next restart'}))
    store = core.Store(root=tmp_path / 'coach')
    _, hooks = mod.build(store=store, checkout=None)
    return hooks, confirms, store


def test_pre_verify_runs_the_second_opinion_for_kanban_cli_and_notice_turns(tmp_path, monkeypatch):
    """The real hook path: screen 0.72 on platforms that keep the 0.75 floor still reaches the second opinion and
    logs report_only_confirm."""
    cases = [('kanban', HUMAN_ASK), ('cli', HUMAN_ASK), ('desktop', NOTICE_ASK), ('desktop', HUMAN_ASK)]
    for i, (plat, ask) in enumerate(cases):
        hooks, confirms, store = _hooks(tmp_path / str(i), monkeypatch, f'coach_init_rd{i}', SCREEN_P)
        sid = f'rd{i}'
        hooks['pre_llm_call'](session_id=sid, user_message=ask, platform=plat)
        hooks['pre_verify'](session_id=sid, platform=plat, attempt=0, final_response=REPLY)
        assert confirms, (plat, ask)
        events = [r.get('event') for r in store.read_log()]
        assert 'report_only_confirm' in events, (plat, ask, events)

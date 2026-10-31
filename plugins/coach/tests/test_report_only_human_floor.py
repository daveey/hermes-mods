"""Human turns reach the report_only second opinion at a lower screen floor than notices; caveat endings are candidates.
Synthetic fixtures only; private replay data must never enter this repo."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('human_floor_rc', Path(__file__).resolve().parents[1] / 'reply_check.py')
assert spec is not None and spec.loader is not None
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

HUMAN_ASK = 'Why is the nightly export failing?'
NOTICE_ASK = '[chat-closeout] Workers settled. Review the export failure.'
REPLY = 'The export fails because the temp dir fills up. The cleanup is not fixed.'


def _llm(p):
    return lambda system, prompt: json.dumps({'handoff': 0, 'confirm': 0, 'unverified': 0, 'unwatched': 0,
                                              'report_only': p, 'evidence': 'screen'})


def _run(ask, p):
    asked = []
    hit = rc.check(REPLY, ask, [], _llm(p), only=['report_only'],
                   thresholds={'report_only': 0.75, 'report_only_screen': 0.85},
                   confirm=lambda: asked.append(1) or (0.9, 'confirmed'),
                   confirm_floor=rc.CONFIRM_FLOOR, human_confirm_floor=rc.HUMAN_CONFIRM_FLOOR)
    return hit, asked


def test_floor_values():
    assert rc.HUMAN_CONFIRM_FLOOR == 0.60 and rc.CONFIRM_FLOOR == 0.75


def test_human_turn_gets_second_opinion_at_0_65():
    hit, asked = _run(HUMAN_ASK, 0.65)
    assert asked and hit and hit['p'] == 0.9


def test_notice_turn_keeps_the_higher_floor():
    hit, asked = _run(NOTICE_ASK, 0.65)
    assert not asked and hit is None


def test_human_below_new_floor_still_skipped():
    hit, asked = _run(HUMAN_ASK, 0.55)
    assert not asked and hit is None


def test_without_human_floor_param_behaviour_unchanged():
    asked = []
    hit = rc.check(REPLY, HUMAN_ASK, [], _llm(0.65), only=['report_only'],
                   thresholds={'report_only': 0.75, 'report_only_screen': 0.85},
                   confirm=lambda: asked.append(1) or (0.9, 'x'), confirm_floor=rc.CONFIRM_FLOOR)
    assert hit is None and not asked


def test_caveat_endings_are_report_only_candidates():
    for t in ('Part one is done. I did not look at the worker logs.',
              'The first queue is repaired. I have not tested the retry path.',
              'Fixed the cache. Say if you want the second one closed too.',
              'Fixed the cache. Let me know if you want the other three migrated.',
              'Patched it. One limit to know: the other store is only writable from the laptop.',
              "It should work if the widget allows storage; I haven't checked that."):
        assert 'report_only' in rc.prefilter(t), t


def test_plain_done_reply_is_not_a_candidate():
    assert 'report_only' not in rc.prefilter('Merged and deployed; the dashboard shows the new build.')


def test_platform_gate_only_desktop_gets_the_lower_floor():
    assert rc.human_confirm_floor('desktop') == rc.HUMAN_CONFIRM_FLOOR
    for plat in ('kanban', 'cli', 'tui', 'subagent', 'cron', '', None):
        assert rc.human_confirm_floor(plat) is None, plat


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
        {'outcome': 'report_only', 'agent_fixable': True, 'quote': 'not fixed'}))
    store = core.Store(root=tmp_path / 'coach')
    _, hooks = mod.build(store=store, checkout=None)
    return hooks, confirms, store


def test_pre_verify_applies_the_lower_floor_to_desktop_human_turns_only(tmp_path, monkeypatch):
    """Screen p 0.65: a Desktop human turn gets the second opinion (and its nudge); the same turn from a Kanban worker,
    CLI, TUI or subagent does not, and neither does a Desktop notice turn."""
    cases = [('desktop', HUMAN_ASK, True), ('kanban', HUMAN_ASK, False), ('cli', HUMAN_ASK, False),
             ('tui', HUMAN_ASK, False), ('subagent', HUMAN_ASK, False), ('desktop', NOTICE_ASK, False)]
    for i, (plat, ask, expect) in enumerate(cases):
        hooks, confirms, store = _hooks(tmp_path / str(i), monkeypatch, f'coach_init_hf{i}', 0.65)
        sid = f'hf{i}'
        hooks['pre_llm_call'](session_id=sid, user_message=ask, platform=plat)
        out = hooks['pre_verify'](session_id=sid, platform=plat, attempt=0, final_response=REPLY)
        assert bool(confirms) is expect, (plat, ask)
        assert bool(out and out.get('action') == 'continue') is expect, (plat, ask)

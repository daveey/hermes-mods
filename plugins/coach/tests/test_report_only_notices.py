"""Synthetic fixtures; private replay data must never enter this repo."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('notice_rc', Path(__file__).resolve().parents[1] / 'reply_check.py')
assert spec is not None and spec.loader is not None
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def test_notice_detection_is_anchored():
    for ask in ('[chat-closeout] Workers settled.', '✔ [default] @worker Kanban t_example done — Repair cache',
                '[False-stop recovery] A notice was missed.', '[owner-wake] Worker needs a decision.'):
        assert rc.notice_turn(ask)
    for ask in ('What does [chat-closeout] mean?', 'Explain a Kanban completion notice', ''):
        assert not rc.notice_turn(ask)


def test_notice_prompt_preserves_closeout_instruction():
    ask = '[chat-closeout] ' + 'worker details ' * 250 + '\nFix the migration gap now.'
    p = rc.confirm_prompt(ask, [], 'The parser is fixed. Live delivery remains untested.')
    for text in ('Fix the migration gap now.', 'AUTOMATED NOTICE', 'each unresolved', 'recent progress', 'live check'):
        assert text in p


def test_human_confirm_prompt_unchanged():
    ask, reply = 'Why does the cache fail?', 'The refresh job is not deployed.'
    assert rc.confirm_prompt(ask, [], reply) == (
        f"USER'S REQUEST (latest):\n{ask}\n\nTOOL LOG THIS TURN (oldest first, real arguments, "
        f"truncated):\n(no tool calls this turn)\n\nAGENT'S FINAL MESSAGE:\n{reply}")


def test_below_threshold_scores_are_auditable():
    events = []
    v = {r: 0.0 for r in rc.RULES}
    v.update(report_only=0.35)
    assert rc.check('The cache is still broken.', 'Fix the cache.', [], lambda *a: json.dumps(v),
                    only=['report_only'], audit=events.append,
                    confirm=lambda: (0.9, 'leftover'), confirm_floor=0.75) is None
    e = next(e for e in events if e['event'] == 'report_only_scored')
    assert e['screen_p'] == 0.35 and e['confirm_attempted'] is False
    assert e['source'] == 'screen' and e['floor'] == 0.75 and e['passing'] is False
    assert 'evidence' not in e


def test_confirm_and_fallback_scores_are_auditable():
    for result, source, final_p in (((0.1, 'active repair'), 'confirm', 0.1), (None, 'screen_fallback', 0.8)):
        events = []
        v = {r: 0.0 for r in rc.RULES}
        v.update(report_only=0.8)
        rc.check('The cache is still broken.', 'Fix it.', [], lambda *a: json.dumps(v),
                 only=['report_only'], audit=events.append, confirm=lambda: result,
                 thresholds={'report_only': 0.75, 'report_only_screen': 0.85}, confirm_floor=0.5,
                 report_only_fallback='nudge')
        e = next(e for e in events if e['event'] == 'report_only_scored')
        assert e['confirm_attempted'] is True and e['source'] == source
        assert e['final_p'] == final_p and not e['passing']


def test_scope_limits_still_prevent_notice_confirm():
    called = []
    assert rc.check('The cache is still broken.', '[chat-closeout] analysis only; do not change anything.',
                    [], lambda *a: called.append('judge'), only=['report_only'],
                    confirm=lambda: called.append('confirm')) is None
    assert called == []


def test_telemetry_failure_does_not_change_verdict():
    v = {r: 0.0 for r in rc.RULES}
    v.update(report_only=0.95)
    def broken(event):
        raise OSError('synthetic telemetry failure')
    hit = rc.check('The cache is still broken.', 'Fix it.', [], lambda *a: json.dumps(v),
                   only=['report_only'], audit=broken)
    assert hit and hit['rule'] == 'report_only'


def test_hook_logs_scores_for_notice_that_does_not_nudge(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('coach_notice_hook', Path(__file__).resolve().parents[1] / '__init__.py')
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    local_rc = plugin._load('reply_check')
    v = {r: 0.0 for r in local_rc.RULES}
    v.update(report_only=0.35)
    monkeypatch.setenv('COACH_REPLY_RULES', 'report_only')
    monkeypatch.setattr(local_rc, 'aux_judge', lambda *a: json.dumps(v))
    store = plugin._load('core').Store(root=tmp_path / 'coach')
    _, hooks = plugin.build(store=store, checkout=None)
    sid = 'synthetic_notice_session'
    hooks['pre_llm_call'](session_id=sid, user_message='[chat-closeout] Workers settled.')
    assert hooks['pre_verify'](session_id=sid, final_response='The cache is still broken.') is None
    events = [json.loads(l) for l in (tmp_path / 'coach/log.jsonl').read_text().splitlines()]
    e = next(e for e in events if e['event'] == 'report_only_scored')
    assert e['session_id'] == sid and e['notice'] is True and isinstance(e['turn'], int)
    assert e['screen_p'] == 0.35 and e['passing'] is False

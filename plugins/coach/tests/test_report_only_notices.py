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


# ---- cross-session relays (agent-83837b58db) ------------------------------------------------------------------------
# A helper session's progress report arrives in its parent chat as a user-role turn opening with core's
# CROSS_SESSION_HEADER. It is Hermes-written, not David asking now.
RELAY = ('[Message from another Hermes session "Subagent: Diagnose worker idleness" (@session:default/example)]\n\n'
         'Correction after reading the prior repair: the stale interpreter is not yet proven to be the defect.')


def test_cross_session_relay_is_an_automation_notice():
    assert rc.notice_turn(RELAY) and rc.automation_prompt(RELAY)
    assert rc.notice_turn('[Message from another Hermes session] bare header')
    for ask in ('Why did I get a [Message from another Hermes session "x"] in this chat?',
                'Message from another Hermes session: is that you?',
                '[Message from another Hermes sessionality] not the header'):
        assert not rc.notice_turn(ask) and not rc.automation_prompt(ask)


def test_relay_confirm_prompt_carries_notice_context():
    p = rc.confirm_prompt(RELAY, [], 'The helper is still investigating; nothing else to do.')
    assert p.startswith(rc.NOTICE_CONTEXT)
    assert 'progress report' in rc.NOTICE_CONTEXT


def test_relay_status_only_skipped_and_uses_notice_floor():
    log = ["-> kanban_show({'task_id': 't_example'})"]
    assert rc.status_only(RELAY + '\nWhat is the status? Are you done?', 'Still running.', log) is None
    seen = {}
    v = {r: 0.0 for r in rc.RULES}
    v.update(report_only=0.7)
    rc.check('The worker is still idle.', RELAY, [], lambda *a: json.dumps(v), only=['report_only'],
             confirm=lambda: seen.setdefault('confirm', (0.1, 'owner active')), confirm_floor=0.75,
             human_confirm_floor=0.60)
    assert 'confirm' not in seen  # 0.7 is under the notice floor 0.75; a human turn (0.60) would confirm


def _core_header():
    import os
    import re as _re
    root = Path(os.environ.get('HERMES_TEST_CORE') or Path.home() / '.hermes/hermes-agent')
    src = root / 'tui_gateway/human_input.py'
    if not src.exists():
        if os.environ.get('HERMES_TEST_CORE'):
            raise AssertionError(f'HERMES_TEST_CORE set but {src} is missing')
        import pytest
        pytest.skip('Hermes core checkout not found')
    m = _re.search(r'^CROSS_SESSION_HEADER\s*=\s*"([^"]+)"', src.read_text(), _re.M)
    assert m, f'CROSS_SESSION_HEADER not defined in {src}'
    return m.group(1)


def test_relay_header_matches_core():
    assert rc.CROSS_SESSION_HEADER == _core_header()
    assert rc.notice_turn(_core_header() + ' "x"]\nhello')


def _self_heal():
    spec = importlib.util.spec_from_file_location('notice_sh', Path(__file__).resolve().parents[1] / 'self_heal.py')
    assert spec is not None and spec.loader is not None
    sh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sh)
    return sh


def _top_level_alternatives(pattern: str) -> list:
    """Split SYNTHETIC_RE's outer `^\\s*(?: a | b | ... )` group into its top-level branches."""
    body = pattern[pattern.index('(?:') + 3:pattern.rindex(')')]
    out, depth, cls, esc, cur = [], 0, False, False, ''
    for ch in body:
        if esc:
            esc = False
        elif ch == '\\':
            esc = True
        elif cls:
            cls = ch != ']'
        elif ch == '[':
            cls = True
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        elif ch == '|' and depth == 0:
            out.append(cur)
            cur = ''
            continue
        cur += ch
    return out + [cur]


# Every SYNTHETIC_RE branch, one sample each, with how reply_check treats it. True = notice_turn must match (Hermes
# automation the parent agent answers). A string = why that envelope is deliberately NOT a reply_check notice.
SYNTHETIC_SAMPLES = {
    RELAY: True,
    '[chat-closeout] Workers settled.': True,
    '⏸️ [default] @worker Kanban t_0123abcd blocked — needs a decision': 'blocked notice asks the human-facing agent to act on a human ask; judged as a live ask',
    '[IMPORTANT: The user launched this session with a skill]': 'skill/system preamble wraps the human\'s own request',
    '[coach] Your reply stopped at a diagnosis.': 'the coach\'s own nudge; judged via reply_nudge_outcome',
    "You've reached the maximum number of tool calls": 'iteration-cap continuation, not a request',
    '✔ [default] @worker Kanban t_example done — Repair cache': True,
}


def test_every_synthetic_envelope_is_classified_for_reply_check():
    sh = _self_heal()
    assert sh.CROSS_SESSION_HEADER == rc.CROSS_SESSION_HEADER
    branches = _top_level_alternatives(sh.SYNTHETIC_RE.pattern)
    covered = set()
    for sample, verdict in SYNTHETIC_SAMPLES.items():
        hits = [i for i, b in enumerate(branches) if __import__('re').match(r'\s*(?:' + b + ')', sample)]
        assert hits, f'sample matches no SYNTHETIC_RE branch: {sample!r}'
        covered.update(hits)
        if verdict is True:
            assert rc.notice_turn(sample), f'SYNTHETIC_RE envelope not a reply_check notice: {sample!r}'
        else:
            assert isinstance(verdict, str) and verdict
    missing = [branches[i] for i in range(len(branches)) if i not in covered]
    assert not missing, f'new SYNTHETIC_RE branch(es) need a SYNTHETIC_SAMPLES entry and a notice decision: {missing}'

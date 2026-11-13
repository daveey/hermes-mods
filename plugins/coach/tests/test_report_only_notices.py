"""Synthetic fixtures; private replay data must never enter this repo."""
import importlib.util
import json
from pathlib import Path

import pytest

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


# ---- core's delegation / background-job envelopes (agent-ef895c63a9, agent-cf237a946c) -----------------------------
# Core writes subagent results and background-process exits into the chat as user-role turns. status_only read the
# batch notice's fixed clause "If you are still waiting on siblings" as David asking for status (7 holds, 2 chats).
CORE_ENVELOPES = {
    'batch': '[ASYNC DELEGATION BATCH COMPLETE — deleg_ab12cd]\nA background fan-out unit you dispatched earlier — 2 '
             'subagent(s) — has finished. If you are still waiting on siblings, end your turn after acting on this one.',
    'single': '[ASYNC DELEGATION COMPLETE — deleg_ab12cd]\nA background subagent you dispatched earlier has finished.',
    'failed': '[ASYNC DELEGATION TASK FAILED — deleg_ab12cd, task 2/3]\nOne subagent in a background fan-out failed.',
    'exit': '[IMPORTANT: Background process proc_1a2b completed normally (exit code 0).\nCommand: ls\nOutput:\nx]',
    'watch': '[IMPORTANT: Background process proc_1a2b matched watch pattern "ready".\nCommand: srv\nMatched output:\nok]',
    'heartbeat': '[Background process proc_1a2b heartbeat #3 — still running after 5m (next in 60s when there is new '
                 'output; you will also be told when it exits).\nCommand: make\nOutput since last heartbeat:\nx]',
    'jobs_stopped': '[System note: background job results from this chat were pending when the Hermes backend shut '
                    'down. This stands in for their completion notices.]',
    # core gateway/kanban_wake_fold.py and gateway/session_automation.py _FOLD_HEADER (agent-93c4a84a5c)
    'kanban_fold': '[IMPORTANT: 3 Kanban task updates arrived while this session was busy. Each card below shows its '
                   'state when this batch was built; an earlier premise that no longer holds is relabelled. Treat '
                   'them as one batch and give one consolidated response.]\n\n✔ [default] @default Kanban t_example',
    'process_fold': '[IMPORTANT: 2 background process results arrived while this session was busy. Treat them as one '
                    'batch and give one consolidated response; preserve failures and actionable results.]\n\nx',
}
HUMAN_LOOKALIKES = (
    '[IMPORTANT: The user launched this CLI session with the "five-whys" skill preloaded.]\n\nwhat is the status?',
    '[OUT-OF-BAND USER MESSAGE — a direct message from the user]\nwhy is it still waiting?',
    'Why did I get an [ASYNC DELEGATION BATCH COMPLETE — x] notice? what is going on?',
    '[IMPORTANT: Background processes keep piling up] why is it still slow?',
)


@pytest.mark.parametrize('name', sorted(CORE_ENVELOPES))
def test_core_delegation_and_job_envelopes_are_automation(name):
    ask = CORE_ENVELOPES[name]
    assert rc.notice_turn(ask) and rc.automation_prompt(ask)
    assert rc.notice_turn(' '.join(ask.split())[:40]), 'the 40-char template head must classify the same way'


@pytest.mark.parametrize('ask', HUMAN_LOOKALIKES)
def test_human_turns_mentioning_envelopes_stay_human(ask):
    assert not rc.notice_turn(ask) and not rc.automation_prompt(ask)


def test_status_only_skips_a_delegation_batch_turn():
    log = ["-> kanban_show({'task_id': 't_example'})"]
    assert rc.PROGRESS_ASK_RE.search(CORE_ENVELOPES['batch'])  # the fixed clause that caused the holds
    assert rc.status_only(CORE_ENVELOPES['batch'], 'Waiting on the other units.', log) is None


def _core_root():
    import os
    root = Path(os.environ.get('HERMES_TEST_CORE') or Path.home() / '.hermes/hermes-agent')
    if not (root / 'agent/context_compressor.py').exists():
        if os.environ.get('HERMES_TEST_CORE'):
            raise AssertionError(f'HERMES_TEST_CORE set but {root} has no agent/context_compressor.py')
        pytest.skip('Hermes core checkout not found')
    return root


def _core_render():
    """Core's own formatters' output, rendered in a child interpreter so core's `tools` package never shadows ours."""
    import subprocess
    import sys
    root = _core_root()
    code = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
import tools.process_registry_notifications as n
from tui_gateway.exit_job_receipts import jobs_stopped_note
now = time.time()
evt = {"results": [{"task_index": 0, "status": "completed", "summary": "ok"}], "goals": ["g"], "dispatched_at": now - 60}
out = {
  "batch": n._format_batch_delegation(evt, "deleg_ab12cd", now),
  "single": n._format_async_delegation({"delegation_id": "deleg_ab12cd", "status": "completed", "summary": "ok",
                                        "goal": "g", "dispatched_at": now - 60, "completed_at": now}),
  "failed": n._format_task_failure_notice({"results": [{"task_index": 1, "status": "failed", "error": "boom"}],
                                           "goals": ["a", "b"]}, "deleg_ab12cd"),
  "exit": n.format_process_notification({"type": "completion", "session_id": "proc_1a2b", "exit_code": 0,
                                         "command": "ls", "output": "x"}),
  "watch": n.format_process_notification({"type": "watch_match", "session_id": "proc_1a2b", "pattern": "ready",
                                          "command": "srv", "output": "ok"}),
  "jobs_stopped": jobs_stopped_note([]),
}
print(json.dumps(out))
'''
    r = subprocess.run([sys.executable, '-c', code, str(root)], capture_output=True, text=True, timeout=60)
    if r.returncode:
        pytest.skip(f'core formatters not importable with {sys.executable}: {r.stderr.strip()[-300:]}')
    return json.loads(r.stdout)


def test_core_rendered_envelopes_are_automation_and_skip_status_only():
    log = ["-> kanban_show({'task_id': 't_example'})"]
    for name, text in _core_render().items():
        assert text, name
        assert rc.notice_turn(text) and rc.automation_prompt(text), f'core {name} envelope not a notice: {text[:80]!r}'
        assert rc.status_only(text, 'Still waiting on the rest.', log) is None, name


def test_every_core_synthetic_user_prefix_is_classified():
    """Contract: each prefix in core's _SYNTHETIC_USER_ROW_PREFIXES is a notice (a sample here) or listed in
    reply_check.NOT_NOTICES with a reason. A new core envelope fails here instead of causing live holds."""
    import ast
    src = (_core_root() / 'agent/context_compressor.py').read_text()
    node = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Assign)
                and any(getattr(t, 'id', '') == '_SYNTHETIC_USER_ROW_PREFIXES' for t in n.targets))
    prefixes = ast.literal_eval(node.value)
    assert prefixes
    samples = {'[ASYNC DELEGATION': CORE_ENVELOPES['batch'], '[IMPORTANT: Background': CORE_ENVELOPES['exit']}
    unclassified = []
    for p in prefixes:
        if p in rc.NOT_NOTICES:
            assert rc.NOT_NOTICES[p].strip(), p
        elif p in samples:
            assert samples[p].startswith(p) and rc.notice_turn(samples[p]), p
        else:
            unclassified.append(p)
    assert not unclassified, f'classify in reply_check.NOTICE_RE (+ a sample) or NOT_NOTICES: {unclassified}'


ENVELOPE_OPENINGS = ('[IMPORTANT:', '[ASYNC')
ENVELOPE_DIRS = ('gateway', 'tools', 'tui_gateway')


def _core_envelope_constants():
    """Every module-level string constant under core's gateway/, tools/, tui_gateway/ that opens like a user-role
    envelope, rendered with its {fields} filled (count=3 etc.): {'<path>:<NAME>': text}."""
    import ast
    import string
    root = _core_root()
    out = {}
    for d in ENVELOPE_DIRS:
        for path in sorted((root / d).rglob('*.py')):
            try:
                tree = ast.parse(path.read_text())
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in tree.body:
                targets = node.targets if isinstance(node, ast.Assign) else \
                    [node.target] if isinstance(node, ast.AnnAssign) else []
                value = getattr(node, 'value', None)
                if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                    continue
                text = value.value
                if not text.lstrip().startswith(ENVELOPE_OPENINGS):
                    continue
                fields = {f for _, f, _, _ in string.Formatter().parse(text) if f}
                text = text.format(**{f: '3' for f in fields})
                for t in targets:
                    out[f'{path.relative_to(root)}:{getattr(t, "id", "?")}'] = text
    return out


def test_core_fold_headers_are_notices_at_full_length_and_template_head():
    """agent-93c4a84a5c: core's folded Kanban wake ("[IMPORTANT: 3 Kanban task updates arrived …") was read as David
    asking live; report_only's holds on it were filed as a template misfire."""
    consts = _core_envelope_constants()
    folds = {k: v for k, v in consts.items() if k.endswith(':_FOLD_HEADER')}
    assert 'gateway/kanban_wake_fold.py:_FOLD_HEADER' in folds, sorted(consts)
    for name, text in folds.items():
        assert rc.notice_turn(text) and rc.automation_prompt(text), name
        assert rc.notice_turn(' '.join(text.split())[:40]), f'{name}: 40-char template head must classify too'
    assert rc.notice_turn('[IMPORTANT: 3 Kanban task updates arrive')
    assert not rc.notice_turn('[IMPORTANT: The user launched this CLI session with the "five-whys" skill')
    assert not rc.notice_turn('[IMPORTANT: 3 Kanban task updates? why so many')


def test_status_only_skips_a_folded_kanban_wake():
    """The folded wake carries card texts ("still waiting on …"); it is not David asking for status."""
    log = ["-> kanban_show({'task_id': 't_example'})"]
    ask = CORE_ENVELOPES['kanban_fold'] + ' — still waiting on review'
    assert rc.PROGRESS_ASK_RE.search(ask)
    assert rc.status_only(ask, 'Waiting on the other cards.', log) is None


def test_every_core_envelope_constant_is_classified():
    """Detection guard: a module-level "[IMPORTANT:"/"[ASYNC" constant in core's gateway/, tools/ or tui_gateway/ is
    a user-role envelope. It must be a notice or listed in reply_check.NOT_NOTICES, so a new gateway envelope fails
    CI instead of causing live holds (agent-93c4a84a5c: the Kanban wake fold header was registered nowhere)."""
    consts = _core_envelope_constants()
    assert consts, 'scan found no envelope constants: the scan or core layout broke'
    unclassified = [k for k, text in consts.items()
                    if not rc.notice_turn(text) and not any(text.startswith(p) for p in rc.NOT_NOTICES)]
    assert not unclassified, f'classify in reply_check.CORE_JOB_RE or NOT_NOTICES: {unclassified}'


# ── resume preambles (agent-6b76c58161) ──────────────────────────────────────────────────────────────────────
# Core wraps a resumed turn's real request in a "[System note: …]" preamble. The coach's template detector keys on
# the ask's opening, so it must see the request, not the preamble. Built from core's own builders: a new or reworded
# core preamble fails here before it reaches live data.

def _core_preambles(prompt):
    import subprocess
    import sys
    root = _core_root()
    code = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
p = sys.argv[2]
import tui_gateway.server as server  # session_auto_continue's bodies run rebound onto server's globals
_auto_continue_note = server._auto_continue_note
from tui_gateway.update_pause import pause_note
from gateway.run import build_resume_recovery_note
from tui_gateway.exit_job_receipts import jobs_stopped_note
out = {"jobs_stopped_report": jobs_stopped_note([{"session_id": "proc_1a2b", "command": "sleep 9", "started_at": 0,
                                                  "kill_state": "killed"}]),"auto_continue": _auto_continue_note(p), "pause": pause_note(p),
       "gateway_restart": build_resume_recovery_note("restart_timeout", p),
       "gateway_noninteractive": build_resume_recovery_note("shutdown_timeout", p, interactive=False)}
open(sys.argv[3], "w").write(json.dumps(out))  # not stdout: core's server import takes stdout for JSON-RPC
'''
    import os
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix='coach-preamble-'))
    env = {**os.environ, 'HERMES_HOME': str(tmp / 'home'), 'HERMES_DISABLE_LAZY_INSTALLS': '1'}
    r = subprocess.run([sys.executable, '-c', code, str(root), prompt, str(tmp / 'out.json')], capture_output=True,
                       text=True, timeout=120, env=env)
    if r.returncode:
        pytest.skip(f'core preamble builders not importable with {sys.executable}: {r.stderr.strip()[-300:]}')
    return json.loads((tmp / 'out.json').read_text())


def test_core_resume_preambles_strip_to_the_real_request():
    notes = _core_preambles('fix X')
    receipt = notes.pop('jobs_stopped_report')
    assert set(notes) == {'auto_continue', 'pause', 'gateway_restart', 'gateway_noninteractive'}
    # core's jobs-stopped notice also opens with a note, then Hermes's job report: a notice, never stripped
    assert '\n\n' in receipt and rc.notice_turn(receipt) and rc.strip_preamble(receipt) == receipt
    for name, note in notes.items():
        assert note.startswith('[System note:'), name
        assert rc.strip_preamble(note) == 'fix X', (name, note[:120])
        assert not rc.automation_prompt(note), f'{name}: a resumed human request must stay judged'


def test_stacked_preambles_strip_and_bare_preambles_stay():
    inner = _core_preambles('fix X')['auto_continue']
    assert rc.strip_preamble(_core_preambles(inner)['pause']) == 'fix X'
    bare = '[System note: The previous turn was interrupted by a gateway restart.]'
    assert rc.strip_preamble(bare) == bare  # nothing real follows: unchanged
    for ask in ('fix X', '', 'What does [System note: x] mean?', '[System note: unterminated'):
        assert rc.strip_preamble(ask) == ask

"""BS-no-progress (skill session-progress): one test per verdict, plus replays of the two real cases of 2026-11-27.

Each test runs the real scanner tick after tick over a board built with core's kanban_db, persisting the snapshots
between ticks as run.py does, and reads the per-chat verdict and the findings.
"""
import json

import pytest

pytest.importorskip('hermes_cli.kanban_db')

from sr_helpers import PLUGIN, Env, fixtures, load  # noqa: E402

F = fixtures()
P = load('_sr_progress', PLUGIN / 'progress.py')
RULE = 'BS-no-progress'
M = 60


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    # No live background processes unless a test says so (the host's own registry is not the fixture's).
    monkeypatch.setattr(e.scan_mod, '_process_chats', lambda src: set())
    monkeypatch.setattr(e.scan_mod, 'chat_processes', lambda src, chats: {})
    return e


def tick(env, now):
    r = env.scan(now=now, rules=[RULE])
    assert not [x for x in r['errors'] if 'progress' in x['where']], r['errors']
    P.persist(r.pop('_progress_snapshots'), env.home, now)
    return r


def verdict(r, chat=F.CHAT):
    return r['progress']['by_chat'].get(chat)


def hits(r, card=None, chat=None):
    return [f for f in r['findings'] if f['id'] == RULE and (card is None or f['card'] == card)
            and (chat is None or f['chat'] == chat)]


def running_with_event(conn, now, *, event_s):
    tid = F.running_healthy(conn, now)
    F.event(conn, tid, 'commented', {'author': 'default', 'comment_id': 1}, at=now - event_s)
    return tid


# ── the verdicts ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_terminal_restricted_parent_does_not_predict_promotion(env):
    from hermes_cli import kanban_db
    parent = F.card(env.conn, 'Archived prerequisite under an independent stop', status='archived')
    tid = F.card(env.conn, 'Still dependency gated', status='todo')
    env.conn.execute("INSERT INTO task_links(parent_id, child_id) VALUES (?, ?)", (parent, tid))
    env.conn.execute('UPDATE tasks SET completed_at=? WHERE id=?', (int(env.now - 5 * 86400), parent))
    env.conn.commit()
    F.event(env.conn, parent, 'decision_hold_restricted', {'cause': 'scope_stopped'}, at=env.now - 4 * 86400)
    assert kanban_db.parent_blocks(env.conn, parent, 'archived')
    tick(env, env.now)
    for elapsed in (31 * M, 62 * M):
        r = tick(env, env.now + elapsed)
        assert hits(r, card=tid) == []  # neither missed_once nor missed_twice
        assert P.load_snapshot(env.home, F.CHAT)['cards'][tid]['prediction']['due_at'] is None


def test_terminal_unrestricted_parent_still_predicts_promotion(env):
    parent = F.card(env.conn, 'Finished prerequisite', status='done')
    tid = F.card(env.conn, 'Needs promotion', status='todo')
    env.conn.execute("INSERT INTO task_links(parent_id, child_id) VALUES (?, ?)", (parent, tid))
    env.conn.execute('UPDATE tasks SET completed_at=? WHERE id=?', (int(env.now - 3600), parent))
    env.conn.commit()
    tick(env, env.now)
    assert hits(tick(env, env.now + 31 * M), card=tid)


def test_fresh_current_run_liveness_defers_board_only_miss_without_claiming_progress(env):
    tid = F.running_quiet(env.conn, env.now)
    tick(env, env.now)
    for elapsed in (31 * M, 62 * M):
        now = env.now + elapsed
        env.conn.execute("UPDATE task_runs SET last_heartbeat_at=? WHERE task_id=? AND status='running'",
                         (int(now - 30), tid))
        env.conn.commit()
        r = tick(env, now)
        assert hits(r, card=tid) == []
        snap = P.load_snapshot(env.home, F.CHAT)
        assert not snap['signals'], 'automatic liveness is not semantic progress'
        assert snap['cards'][tid]['prediction']['deferred'] == 'runtime_activity_unobserved'
        assert snap['cards'][tid]['misses'] == 0
    # Once the runtime stops refreshing, ordinary overdue diagnosis still applies.
    assert hits(tick(env, env.now + 100 * M), card=tid)


def test_continuous_heartbeat_is_eventually_runtime_unverified_not_a_proven_stall(env):
    tid = F.running_quiet(env.conn, env.now, quiet_s=0)
    tick(env, env.now)
    diagnosed = []
    for minutes in range(15, 481, 15):
        now = env.now + minutes * M
        env.conn.execute("UPDATE task_runs SET last_heartbeat_at=? WHERE task_id=? AND status='running'",
                         (int(now - 30), tid))
        env.conn.commit()
        F.event(env.conn, tid, 'heartbeat', {}, at=now - 30)
        r = tick(env, now)
        found = hits(r, card=tid)
        assert not P.load_snapshot(env.home, F.CHAT)['signals']
        if minutes < 240:
            assert found == [], 'row-618 shape (~100min) must remain deferred'
        elif minutes == 240:
            assert found, 'automatic liveness must not hide board silence forever'
        if found:
            diagnosed.extend(found)
            for f in found:
                assert f['variant'] == 'runtime_unverified'
                assert f['fix']['kind'] == 'card', 'investigate; do not wake/reclaim an active worker'
                assert f['evidence']['runtime_live'] is True
                assert f['evidence']['runtime_progress'] == 'unknown'
                assert f['evidence']['board_silent_s'] >= 4 * 3600
                assert 'runtime progress unknown' in f['summary']
    assert diagnosed


def test_runtime_ceiling_survives_a_snapshot_restart_and_real_progress_rearms_it(env):
    tid = F.running_quiet(env.conn, env.now, quiet_s=4 * 3600)
    # A new baseline must not grant another four hours to an old live run.
    for minutes in (0, 15):
        now = env.now + minutes * M
        env.conn.execute("UPDATE task_runs SET last_heartbeat_at=? WHERE task_id=?", (int(now - 30), tid))
        env.conn.commit()
        r = tick(env, now)
    assert hits(r, card=tid)[0]['variant'] == 'runtime_unverified'
    F.event(env.conn, tid, 'commented', {'author': 'default', 'comment_id': 77}, at=env.now + 20 * M)
    for minutes in (30, 61, 120):
        now = env.now + minutes * M
        env.conn.execute("UPDATE task_runs SET last_heartbeat_at=? WHERE task_id=?", (int(now - 30), tid))
        env.conn.commit()
        assert hits(tick(env, now), card=tid) == []
    snap = P.load_snapshot(env.home, F.CHAT)
    assert snap['cards'][tid]['prediction']['deferred'] == 'runtime_activity_unobserved'


def test_runtime_ceiling_cannot_override_an_execution_hold():
    now = 50_000
    obs = _obs(held='spend_decision', runtime_liveness_at=now - 30, runtime_started_at=1)
    cur = {'chat': 'c', 'cards': {'t': obs}, 'last_message_id': 1, 'processes': {}}
    a = P.evaluate(None, cur, now, long_wait={})
    obs['runtime_liveness_at'] = now + 900 - 30
    b = P.evaluate(a['snapshot'], cur, now + 900, long_wait={})
    assert not b['missed']
    assert b['snapshot']['cards']['t']['prediction']['terminal'] == 'held'


@pytest.mark.parametrize('offset', [-301, 1])
def test_stale_or_future_heartbeat_does_not_defer_running_miss(env, offset):
    tid = F.running_quiet(env.conn, env.now)
    tick(env, env.now)
    now = env.now + 31 * M
    env.conn.execute("UPDATE task_runs SET last_heartbeat_at=? WHERE task_id=? AND status='running'",
                     (int(now + offset), tid))
    env.conn.commit()
    assert hits(tick(env, now), card=tid)


@pytest.mark.parametrize('invalid', ['ended', 'foreign'])
def test_ended_or_foreign_run_heartbeat_cannot_defer(env, invalid):
    tid = F.running_quiet(env.conn, env.now)
    other = F.card(env.conn, 'Different run owner', status='running') if invalid == 'foreign' else None
    tick(env, env.now)
    now = env.now + 31 * M
    rid = env.conn.execute('SELECT current_run_id FROM tasks WHERE id=?', (tid,)).fetchone()[0]
    env.conn.execute('UPDATE task_runs SET last_heartbeat_at=? WHERE id=?', (int(now - 30), rid))
    if invalid == 'ended':
        env.conn.execute("UPDATE task_runs SET status='done' WHERE id=?", (rid,))
    else:
        env.conn.execute('UPDATE task_runs SET task_id=? WHERE id=?', (other, rid))
    env.conn.commit()
    assert hits(tick(env, now), card=tid)


def test_first_sight_is_a_baseline_and_judges_nothing(env):
    F.running_quiet(env.conn, env.now)
    r = tick(env, env.now)
    assert verdict(r) == 'baseline' and hits(r) == []
    snap = P.load_snapshot(env.home, F.CHAT)
    assert snap['predictions'] and all(p['due_at'] for p in snap['predictions'])


def test_progress_card_done(env):
    running_with_event(env.conn, env.now, event_s=60)
    b = F.long_wait(env.conn, env.now, kind='ci')
    tick(env, env.now)
    env.conn.execute("UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?", (int(env.now + 300), b))
    env.conn.commit()
    r = tick(env, env.now + 15 * M)
    assert verdict(r) == 'progress' and hits(r) == []
    sig = P.load_snapshot(env.home, F.CHAT)['signals']
    assert {'card': b, 'signal': 'status', 'from': 'scheduled', 'to': 'done'} in sig


def test_progress_non_heartbeat_event_resets_the_window(env):
    tid = F.running_quiet(env.conn, env.now)
    tick(env, env.now)
    F.event(env.conn, tid, 'heartbeat', {}, at=env.now + 10 * M)  # liveness only: not progress
    r = tick(env, env.now + 15 * M)
    assert verdict(r) == 'pending'
    F.event(env.conn, tid, 'heartbeat', {'note': 'tests pass, opening the PR'}, at=env.now + 20 * M)
    r = tick(env, env.now + 31 * M)
    assert verdict(r) == 'progress' and hits(r) == [], 'an agent-written heartbeat note is progress'


def test_healthy_running(env):
    running_with_event(env.conn, env.now, event_s=5 * M)
    tick(env, env.now)
    r = tick(env, env.now + 15 * M)
    assert verdict(r) == 'healthy_running' and hits(r) == []


def test_healthy_ci_wait(env):
    tid = F.long_wait(env.conn, env.now, waited_s=10 * M, kind='ci')
    tick(env, env.now)
    r = tick(env, env.now + 15 * M)
    assert verdict(r) == 'healthy_wait' and hits(r) == []
    pred = P.load_snapshot(env.home, F.CHAT)['cards'][tid]['prediction']
    # due at park + expected (BS-long-wait's 45 min for ci) + grace, not at +30 min
    assert pred['due_at'] == pytest.approx(env.now - 10 * M + 45 * M + P.CI_GRACE_S, abs=2)
    r = tick(env, env.now + 51 * M)
    assert verdict(r) == 'long_wait' and hits(r) == [], 'an overdue producer park is BS-long-wait\'s to flag'
    assert P.load_snapshot(env.home, F.CHAT)['cards'][tid]['prediction']['deferred'] == 'BS-long-wait'
    assert [f['id'] for f in env.scan(now=env.now + 51 * M)['findings'] if f['card'] == tid] == ['BS-long-wait']


def test_clock_park_not_due(env):
    tid = F.card(env.conn, 'Parked until tonight', status='scheduled')
    F.age_all_events(env.conn, tid, env.now - 600)
    F.event(env.conn, tid, 'scheduled', {'wake_at': int(env.now + 3 * 3600), 'reason': 'nightly build'},
            at=env.now - 600)
    tick(env, env.now)
    for k in (1, 2, 4, 8):  # two hours with nothing new: a clock park is due at its wake, not at +30 min
        r = tick(env, env.now + k * 15 * M)
        assert verdict(r) == 'clock_not_due' and hits(r) == [], k
    pred = P.load_snapshot(env.home, F.CHAT)['cards'][tid]['prediction']
    assert pred['due_at'] == pytest.approx(env.now + 3 * 3600 + P.CLOCK_GRACE_S, abs=2)
    r = tick(env, env.now + 3 * 3600 + 15 * M)
    assert verdict(r) == 'missed_once', 'the wake did not fire'


def test_valid_red(env):
    F.blocked_valid_ask(env.conn, env.now)
    tick(env, env.now)
    for k in (1, 4, 12):
        r = tick(env, env.now + k * 15 * M)
        assert verdict(r) == 'valid_red' and hits(r) == []


def test_triage_card_is_a_decision_queue_not_a_miss(env):
    F.card(env.conn, 'Retro exact-head review awaiting triage', status='triage')
    tick(env, env.now)
    for k in (1, 2, 4, 8):
        r = tick(env, env.now + k * 15 * M)
        assert verdict(r) == 'valid_red' and hits(r) == [], k


def test_invalid_red_is_not_terminal(env):
    tid = F.blocked_invalid_ask(env.conn, env.now)
    tick(env, env.now)
    r = tick(env, env.now + 31 * M)
    assert verdict(r) == 'missed_once' and hits(r, card=tid)


def test_missed_once_nudges(env):
    tid = F.running_quiet(env.conn, env.now)
    tick(env, env.now)
    r = tick(env, env.now + 15 * M)
    assert verdict(r) == 'pending' and hits(r) == []
    r = tick(env, env.now + 31 * M)
    assert verdict(r) == 'missed_once'
    (f,) = hits(r, card=tid)
    assert f['variant'] == 'missed_once' and f['fix']['kind'] == 'nudge' and f['area'] == 'card'
    assert f['fingerprint'] == f'{RULE}:default/{tid}' and f['chat'] == F.CHAT
    e = f['evidence']
    assert e['misses'] == 1 and e['status'] == 'running' and 'non-heartbeat event' in e['expect']
    assert e['last_event']['id'] and e['predictions']


def test_missed_twice_files_a_fix_card(env):
    tid = F.running_quiet(env.conn, env.now)
    tick(env, env.now)
    assert verdict(tick(env, env.now + 31 * M)) == 'missed_once'
    r = tick(env, env.now + 46 * M)  # the miss re-arms a full window: no second miss one tick later
    assert hits(r) == [] and verdict(r) == 'pending'
    r = tick(env, env.now + 62 * M)
    assert verdict(r) == 'missed_twice'
    (f,) = hits(r, card=tid)
    assert f['variant'] == 'missed_twice' and f['fix']['kind'] == 'card'
    assert f['fingerprint'] == f'{RULE}:default/{tid}', 'run.py dedupes the fix card per card per 24 h'
    assert 'human' not in f['fix']['action'] and f['evidence']['misses'] == 2


def test_progress_between_misses_resets_the_count(env):
    tid = F.running_quiet(env.conn, env.now)
    tick(env, env.now)
    assert verdict(tick(env, env.now + 31 * M)) == 'missed_once'
    F.event(env.conn, tid, 'commented', {'author': 'default', 'comment_id': 7}, at=env.now + 40 * M)
    assert verdict(tick(env, env.now + 46 * M)) == 'progress'
    r = tick(env, env.now + 77 * M)
    assert verdict(r) == 'missed_once' and hits(r, card=tid)[0]['variant'] == 'missed_once'


def test_a_chat_turn_is_progress(env):
    F.running_quiet(env.conn, env.now)
    tick(env, env.now)
    env.state.execute('INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)',
                      (F.CHAT, 'assistant', 'still on it', env.now + 20 * M))
    env.state.commit()
    r = tick(env, env.now + 31 * M)
    assert verdict(r) == 'progress' and hits(r) == []


def test_session_repairs_own_nudge_is_not_progress(env):
    tid = F.running_quiet(env.conn, env.now)
    tick(env, env.now)
    env.state.execute('INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)',
                      (F.CHAT, 'user', '[session-repair] are you stuck?', env.now + 20 * M))
    env.state.commit()
    r = tick(env, env.now + 31 * M)
    assert verdict(r) == 'missed_once' and hits(r, card=tid)


def test_process_only(env, monkeypatch, tmp_path):
    log = tmp_path / 'proc.log'
    log.write_text('waiting\n')
    procs = {'S2': {'proc_a': {'id': 'proc_a', 'command': 'bb-land 858', 'started_at': env.now - 600,
                               'log': str(log), 'log_mtime': env.now - 300}}}
    monkeypatch.setattr(env.scan_mod, '_process_chats', lambda src: {'S2'})
    monkeypatch.setattr(env.scan_mod, 'chat_processes', lambda src, chats: {c: procs[c] for c in chats if c in procs})
    assert verdict(tick(env, env.now), 'S2') == 'baseline'
    r = tick(env, env.now + 15 * M)
    assert verdict(r, 'S2') == 'healthy_wait' and hits(r) == []
    r = tick(env, env.now + 31 * M)
    assert verdict(r, 'S2') == 'process_only'
    (f,) = hits(r, chat='S2')
    assert f['card'] is None and f['variant'] == 'process_only' and f['fix']['kind'] == 'nudge'
    assert f['fingerprint'] == f'{RULE}:chat/S2' and f['evidence']['process_only'] is True
    procs['S2']['proc_a']['log_mtime'] = env.now + 40 * M  # its log grew: progress
    assert verdict(tick(env, env.now + 46 * M), 'S2') == 'progress'


def test_chat_with_cards_ignores_its_processes_for_misses(env, monkeypatch):
    running_with_event(env.conn, env.now, event_s=60)
    monkeypatch.setattr(env.scan_mod, 'chat_processes', lambda src, chats: {F.CHAT: {
        'p': {'id': 'p', 'command': 'sleep', 'started_at': env.now, 'log': None, 'log_mtime': None}}})
    tick(env, env.now)
    r = tick(env, env.now + 15 * M)
    assert verdict(r) == 'healthy_running' and hits(r) == []


def test_another_rules_finding_on_the_card_wins(env):
    tid = F.running_quiet(env.conn, env.now)
    P.persist(env.scan(now=env.now)['_progress_snapshots'], env.home, env.now)
    r = env.scan(now=env.now + 31 * M)
    assert any(f['id'] == 'BS-stuck-run' and f['card'] == tid for f in r['findings'])
    assert hits(r, card=tid) == [] and verdict(r) == 'missed_once', 'the miss is still recorded'


def test_stale_snapshot_is_a_new_baseline(env):
    F.running_quiet(env.conn, env.now)
    tick(env, env.now)
    r = tick(env, env.now + P.STALE_SNAPSHOT_S + 60)
    assert verdict(r) == 'baseline' and hits(r) == []


def test_scan_writes_no_snapshot(env):
    F.running_quiet(env.conn, env.now)
    env.scan(now=env.now, rules=[RULE])
    assert not P.progress_dir(env.home).exists(), 'only run.py persists'


# ── replays of the real cases (2026-11-27, hub board) ────────────────────────────────────────────────────────────
REVIEW_DONE = 1795817942          # t_01a21f0f395684a1 (exact-head review) completed 22:19:02Z
PRODUCER_PARK = 1795817634        # t_01a214179dabda32 scheduled 22:13:54Z until 11-28 10:00Z, awaiting that review
CHAT_PARK = 1795818626            # t_01a21c00a2e628c8 (the chat's card) scheduled 22:30:26Z, awaiting the producer
WAKE_AT = 1795860000              # 2026-11-28T10:00:00Z, both parks


def test_replay_producer_parked_after_its_awaited_review_was_done(env):
    """Chat 20261127_070008_d80ef6: its card waited on producer t_01a214179dabda32, which sat parked until 11-28
    10:00Z after its awaited review t_01a21f0f395684a1 was done. Flagged once 10 min past the review, then again."""
    conn = env.conn
    review = F.card(conn, 'Independent exact-head review (delta re-review)', status='done', session=None,
                    completed_at=REVIEW_DONE)
    producer = F.card(conn, 'Panel freshness producer (UNACTIVATED)', status='scheduled', session=None)
    F.age_all_events(conn, producer, PRODUCER_PARK - 3600)
    F.event(conn, producer, 'scheduled', {
        'reason': f'SCHEDULED_UNTIL=2026-11-28T10:00:00Z — waiting on exact-head delta review {review}',
        'wake_at': WAKE_AT, 'check_every': 900,
        'wake_check': "hermes kanban wait-check key 'core-wt-review:o/r#4625:13882e10' --gateway hub",
        'awaiting': {'kind': 'card', 'ref': review, 'why': 'exact-head delta review', 'gateway': 'hub'}},
        at=PRODUCER_PARK)
    chat_card = F.card(conn, 'Desktop shows a blocked card that no board has', status='scheduled')
    F.age_all_events(conn, chat_card, CHAT_PARK - 3600)
    F.event(conn, chat_card, 'scheduled', {
        'reason': 'SCHEDULED_UNTIL=2026-11-28T10:00:00+00:00 — PR merged; waiting for the backend restart',
        'wake_at': WAKE_AT, 'check_every': 120, 'wake_check': "hub-admin 'test \"$p\" != 3122368'",
        'awaiting': {'kind': 'card', 'ref': producer, 'why': 'activation owner', 'gateway': 'hub'}}, at=CHAT_PARK)
    t0 = CHAT_PARK + 4 * M                                   # 22:34Z: first tick that sees the chat this way
    r = tick(env, t0)
    snap = P.load_snapshot(env.home, F.CHAT)
    assert verdict(r) == 'baseline' and producer in snap['cards'], 'the chain walk reaches the producer'
    assert snap['cards'][producer]['prediction']['due_at'] == REVIEW_DONE + P.AWAITED_DONE_GRACE_S
    assert snap['cards'][chat_card]['prediction'].get('dependent') == producer
    r = tick(env, t0 + 15 * M)                               # 22:49Z
    assert verdict(r) == 'missed_once'
    (f,) = hits(r)
    assert f['card'] == producer and f['fix']['kind'] == 'nudge' and f['chat'] == F.CHAT
    assert f['evidence']['awaited'] == {'kind': 'card', 'ref': review, 'gateway': 'hub'}
    assert f['evidence']['awaited_status'] == 'done'
    assert hits(tick(env, t0 + 30 * M)) == []                # 23:04Z: re-armed
    r = tick(env, t0 + 46 * M)                               # 23:20Z
    assert verdict(r) == 'missed_twice'
    (f,) = hits(r)
    assert f['card'] == producer and f['fix']['kind'] == 'card' and f['variant'] == 'missed_twice'


def test_replay_chat_whose_only_waits_are_two_bare_hub_processes(env, monkeypatch, tmp_path):
    """Chat 20261127_202849_4c4281 ('Add Davy Mac Mini to Git CI'): no card; it declared waiting on two background
    processes (bb-land PR #858, then the land chain) whose logs stopped moving. Flagged process_only, then twice."""
    chat = 'S-ci-mini'
    last_turn = 1795813511                                   # 21:05:11Z: session_outcome waiting on the process
    env.state.execute('INSERT INTO sessions (id, title, started_at) VALUES (?, ?, ?)',
                      (chat, 'Add a Mac mini to Git CI', last_turn - 3600))
    env.state.execute('INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)',
                      (chat, 'tool', json.dumps({'ok': True, 'outcome': {'kind': 'waiting', 'on': [
                          {'kind': 'process', 'id': 'proc_bbd8faf9ece3'}]}}), last_turn))
    env.state.commit()
    logs = {}
    for pid, cmd, started in (('proc_c3c62558b7e2', 'bb-land 858', 1795812626),
                              ('proc_bbd8faf9ece3', 'land chain: #858 -> ci/ios-dispatch-immediate', 1795813481)):
        path = tmp_path / f'{pid}.log'
        path.write_text('queued\n')
        logs[pid] = {'id': pid, 'command': cmd, 'started_at': started, 'log': str(path), 'log_mtime': started + 60}
    monkeypatch.setattr(env.scan_mod, '_process_chats', lambda src: {chat})
    monkeypatch.setattr(env.scan_mod, 'chat_processes', lambda src, chats: {chat: logs} if chat in chats else {})
    t0 = last_turn + 10 * M                                  # 21:15Z
    assert verdict(tick(env, t0), chat) == 'baseline'
    assert verdict(tick(env, t0 + 15 * M), chat) == 'healthy_wait'
    r = tick(env, t0 + 31 * M)                               # 21:46Z
    assert verdict(r, chat) == 'process_only'
    found = hits(r, chat=chat)
    assert {f['variant'] for f in found} == {'process_only'} and all(f['card'] is None for f in found)
    assert len({f['fingerprint'] for f in found}) == 1, 'one chat-level finding per tick'
    r = tick(env, t0 + 62 * M)                               # 22:17Z
    assert verdict(r, chat) == 'missed_twice'
    assert {f['fix']['kind'] for f in hits(r, chat=chat)} == {'card'}


def test_overdue_producer_park_with_a_fresh_healthy_receipt_is_not_a_miss(env, monkeypatch):
    """Review B2: an aged CI/probe park whose watcher keeps reporting healthy (unchanged facts, refreshed receipt)
    is BS-long-wait's healthy producer, not a BS-no-progress miss."""
    monkeypatch.setattr(env.scan_mod.Sources, 'producer_state', lambda *a: {'state': 'not_resolvable', 'happened': False})
    now = int(env.now)
    for kind in ('ci', 'external', None):
        tid = F.card(env.conn, f'Waiting for producer {kind}', status='scheduled')
        F.age_all_events(env.conn, tid, now - 4000)
        park = {'reason': 'producer', 'wake_at': now + 86400, 'wake_check': 'python3 probe.py'}
        if kind:
            park['awaiting'] = {'kind': kind, 'ref': 'job'}
        F.event(env.conn, tid, 'scheduled', park, at=now - 4000)
    def watch(at):
        for (tid,) in env.conn.execute("SELECT id FROM tasks WHERE status = 'scheduled'").fetchall():
            F.event(env.conn, tid, 'external_watch', {
                'schema': 1, 'adapter': 'shell', 'health': 'ok', 'state': 'external_running', 'observed_at': at - 100,
                'next_due_at': at + 800, 'every': 900, 'consecutive_failures': 0, 'failing_since': None}, at=at - 100)
    watch(now)
    tick(env, now)
    for k in (1, 2, 3, 5):
        watch(now + k * 15 * M)
        r = tick(env, now + k * 15 * M)
        assert hits(r) == [] and verdict(r) in ('healthy_wait', 'long_wait'), (k, verdict(r))
        assert not [f for f in r['findings'] if f['id'] == 'BS-long-wait'], 'and BS-long-wait agrees: healthy'


# ── the nudge lane (after the soak) ──────────────────────────────────────────────────────────────────────────────
def test_nudge_revalidates_and_words_a_no_progress_finding(env, monkeypatch):
    nudge = load('_sr_nudge_np', PLUGIN / 'nudge.py')
    tid = F.running_quiet(env.conn, env.now)
    tick(env, env.now)
    (f,) = hits(tick(env, env.now + 31 * M), card=tid)
    real = env.scan_mod.Sources
    readers = env.sources().readers
    monkeypatch.setattr(env.scan_mod, 'Sources', lambda: real(readers, now=env.now + 31 * M))
    current, _src = nudge.fresh(env.scan_mod, f)
    assert current == f
    text = nudge.message(f, [])
    assert text.startswith('[session-repair] No progress seen for 0h 31m') and 'non-heartbeat event' in text
    F.event(env.conn, tid, 'commented', {'author': 'default', 'comment_id': 9}, at=env.now + 32 * M)
    assert nudge.fresh(env.scan_mod, f)[0] is None, 'the owner moved: nothing to nudge'


# ── the pure evaluator ───────────────────────────────────────────────────────────────────────────────────────────
def _obs(**kw):
    base = {'board': 'default', 'status': 'running', 'title': 't', 'run_id': 1, 'event_id': 5, 'event_at': 0,
            'event_kind': 'claimed', 'last_event': {'id': 5}, 'human': False, 'held': None, 'park': None,
            'awaited': None, 'awaited_status': None, 'awaited_done_at': None, 'awaited_observed': False,
            'watch': None, 'healthy': None, 'parents_open': None, 'parents_done_at': None, 'skip': False}
    return {**base, **kw}


def test_merge_queue_position_change_is_progress():
    park = {'id': 9, 'at': 0, 'kind': 'scheduled', 'wake_at': 9e9, 'probe': True, 'expected_s': None,
            'reason': '', 'why': 'merge queue'}
    a = _obs(status='scheduled', park=park, awaited={'kind': 'external', 'ref': 'merge-queue/858'},
             watch=['external_running', 'position 4'])
    cur = {'chat': 'c', 'cards': {'t': a}, 'last_message_id': 1, 'processes': {}}
    first = P.evaluate(None, cur, 1000, long_wait={'external': 3 * 3600})
    assert first['snapshot']['cards']['t']['prediction']['expect'].startswith('merge-queue')
    b = dict(a, watch=['external_running', 'position 2'])
    res = P.evaluate(first['snapshot'], dict(cur, cards={'t': b}), 1000 + 15 * M, long_wait={'external': 3 * 3600})
    assert res['verdict'] == 'progress' and res['signals'][0]['signal'] == 'producer'


def test_snapshot_files_are_atomic_and_pruned(tmp_path):
    P.persist({'c': {'version': P.VERSION, 'chat': 'c', 'at': 1, 'cards': {}}}, tmp_path, 1)
    assert P.load_snapshot(tmp_path, 'c')['chat'] == 'c'
    assert P.load_snapshot(tmp_path, 'other') is None
    old = P.progress_dir(tmp_path) / 'gone.json'
    old.write_text('{}')
    import os
    os.utime(old, (1, 1))
    P.persist({'c': {'version': P.VERSION, 'chat': 'c', 'at': 1, 'cards': {}}}, tmp_path, P.KEEP_S + 10)
    assert not old.exists() and P.load_snapshot(tmp_path, 'c')

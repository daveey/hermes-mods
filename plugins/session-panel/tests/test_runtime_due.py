"""The runtime lane re-captures a chat when ITS process evidence moves, never all chats at once.

A fake processes.json: `procs` (chat -> its entries) is what `runtime_fingerprints` reports per chat;
`version` is the whole file's stamp, moving on every write (also an unchanged rewrite). The earlier
rule made every watched chat due whenever `version` moved and held all of them due while any one
failed or lost a publication race."""
import pytest

from test_feed import make


class Procs:
    def __init__(self, r, clock):
        self.r, self.clock = r, clock
        self.procs = {}
        self.version = 0
        self.calls = []
        self.fail = set()
        r.links = {}
        r.finished = {}
        r.panel_inputs = lambda *a: {'requests': [], 'outcome_json': {}, 'processes': []}
        r.decision_inputs = lambda *a: {'requests': [], 'outcome_json': {}}
        r.panel_files_stamp = lambda: 1
        r.decision_store_stamp = lambda: 1
        r.panel_stamps = lambda ids: {s: 1 for s in ids}
        r.runtime_stamp = lambda: self.version
        r.runtime_fingerprints = lambda lineages: {s: tuple(self.procs.get(s, ())) or None for s in lineages}
        r.runtime_inputs = self.capture

    def capture(self, session, lineage):
        self.calls.append(session)
        if session in self.fail:
            raise OSError('probe failed')
        return {'outcome_json': {}, 'processes': [
            {'id': p, 'title': p, 'execution_observations': [], 'probe_at': self.clock['now']}
            for p in self.procs.get(session, ())]}

    def write(self, **chats):
        """One processes.json write: the named chats' entries replaced, every other chat's unchanged."""
        for s, entries in chats.items():
            self.procs[s] = tuple(entries)
        self.version += 1

    def captured(self, f):
        self.calls.clear()
        f.refresh_decisions()
        return sorted(self.calls)


CHATS = ['S1', 'S2', 'S3']


def setup(catchup=float('inf')):
    f, r, pushed, clock = make()
    f.runtime_catchup_s = catchup  # (a)-(f) isolate the evidence rules; the catch-up has its own tests below
    p = Procs(r, clock)
    f.changes(CHATS)
    assert p.captured(f) == []  # nothing owned, nothing written: no capture
    return f, p, clock


def held(f, s):
    return [x['id'] for x in f.bundles[s]['bundle'].get('processes') or ()]


def test_a_another_chats_process_change_does_not_make_this_chat_due():
    f, p, clock = setup()
    p.write(S1=['proc_a'])
    assert p.captured(f) == ['S1']
    p.write(S2=['proc_b'])
    assert p.captured(f) == ['S2']
    p.write(S2=['proc_b', 'proc_c'])
    assert p.captured(f) == ['S2']
    p.write()  # a rewrite that leaves every chat's evidence unchanged
    assert p.captured(f) == []


def test_b_a_chats_own_process_change_makes_it_due():
    f, p, clock = setup()
    p.write(S1=['proc_a'])
    assert p.captured(f) == ['S1']
    clock['now'] += 5
    p.write(S1=['proc_a2'])  # a field core reads moved
    assert p.captured(f) == ['S1']
    assert held(f, 'S1') == ['proc_a2']


def test_c_gaining_and_losing_the_last_process():
    f, p, clock = setup()
    p.write(S3=['proc_x'])
    assert p.captured(f) == ['S3']
    assert held(f, 'S3') == ['proc_x']
    clock['now'] += 5
    p.write(S3=[])
    assert p.captured(f) == ['S3']
    assert held(f, 'S3') == []
    assert f._runtime_retry_at is None  # no live process left: no timed re-probe
    clock['now'] += 600
    assert p.captured(f) == []


def test_d_one_failing_chat_does_not_keep_the_others_due():
    f, p, clock = setup()
    p.fail.add('S2')
    p.write(S1=['proc_a'], S2=['proc_b'])
    assert p.captured(f) == ['S1', 'S2']
    assert 'S2' in f._decision_failures
    # Passes woken by other events: S1 settled and stays settled; S2 waits on its own backoff.
    tries, gaps = 1, []
    for _ in range(40):
        clock['now'] += 0.05
        calls = p.captured(f)
        assert 'S1' not in calls and 'S3' not in calls
        if calls:
            gaps.append(f._runtime_retry['S2'][1] - clock['now'])
            tries += 1
    assert 1 < tries < 10, 'retried on a backoff, not every pass'
    assert gaps == sorted(gaps), 'the backoff grows'
    p.write(S1=[])  # S1's own change is still seen while S2 fails (and S1 now holds nothing to re-probe)
    assert p.captured(f) == ['S1']
    # Bounded: however long it fails, the next try is at most RUNTIME_REPROBE_S away.
    for _ in range(1200):  # hours of failure: the backoff never overflows
        clock['now'] = f._runtime_retry['S2'][1]
        assert p.captured(f) == ['S2']
    from test_feed import feedmod
    assert f._runtime_retry['S2'][1] - clock['now'] == feedmod.RUNTIME_REPROBE_S
    p.fail.clear()
    clock['now'] = f._runtime_retry['S2'][1]
    assert p.captured(f) == ['S2']
    assert held(f, 'S2') == ['proc_b'] and 'S2' not in f._runtime_retry
    p.write(S3=['proc_c'])
    assert p.captured(f) == ['S3']


def test_e_a_superseded_capture_retries_that_chat_alone():
    f, p, clock = setup()
    capture = p.capture

    def interleaved(session, lineage):
        fresh = capture(session, lineage)
        if session == 'S1':
            f.refresh_decision('S1', readers=p.r)  # a newer decision-only read wins S1's publication
        return fresh

    p.r.runtime_inputs = interleaved
    p.write(S1=['proc_a'], S2=['proc_b'])
    assert p.captured(f) == ['S1', 'S2']
    assert held(f, 'S2') == ['proc_b'] and held(f, 'S1') == []
    assert f._runtime_retry_at == clock['now'] + 1
    p.r.runtime_inputs = capture
    clock['now'] += 0.5
    assert p.captured(f) == []  # the 1 s retry is S1's own clock, not every pass
    clock['now'] += 0.6
    assert p.captured(f) == ['S1']
    assert held(f, 'S1') == ['proc_a']
    assert p.captured(f) == []


def test_f_live_processes_are_reprobed_every_45_seconds_per_chat():
    f, p, clock = setup()
    p.write(S1=['proc_a'])
    assert p.captured(f) == ['S1']
    clock['now'] += 20
    p.write(S2=['proc_b'])
    assert p.captured(f) == ['S2']
    assert f._runtime_retry_at == clock['now'] - 20 + 45
    clock['now'] += 26  # S1 at 46 s, S2 at 26 s
    assert p.captured(f) == ['S1']
    clock['now'] += 20  # S2 at 46 s, S1 at 20 s
    assert p.captured(f) == ['S2']
    p.write(S1=[], S2=[])
    assert p.captured(f) == ['S1', 'S2']
    clock['now'] += 100
    assert p.captured(f) == []  # no held process: no timed probe


def test_unreadable_fingerprints_make_no_chat_due_and_retry_soon():
    f, p, clock = setup()
    fingerprints = p.r.runtime_fingerprints

    def broken(lineages):
        raise OSError('checkpoint unreadable')

    p.r.runtime_fingerprints = broken
    p.write(S1=['proc_a'])
    assert p.captured(f) == []
    assert f._runtime_retry_at == clock['now'] + 1
    p.r.runtime_fingerprints = fingerprints
    clock['now'] += 1
    assert p.captured(f) == ['S1']
    assert f._runtime_retry_at == clock['now'] + 45


def test_forgotten_chats_leave_no_runtime_state():
    f, p, clock = setup()
    p.fail.add('S2')
    p.write(S1=['proc_a'], S2=['proc_b'])
    p.captured(f)
    for s in ('S1', 'S2'):
        f._forget(s)
    p.captured(f)
    assert not (set(f._runtime_seen) | set(f._runtime_fp) | set(f._runtime_retry)) & {'S1', 'S2'}


def test_catchup_recaptures_every_chat_within_one_period_spread_never_all_at_once():
    """Outcome inputs with no event-driven refresh (registered control providers) heal within one period."""
    from test_feed import feedmod
    period = 900.0
    f, r, pushed, clock = make()
    f.runtime_catchup_s = period
    p = Procs(r, clock)
    chats = [f'C{i:02d}' for i in range(60)]
    f.changes(chats)
    start = clock['now']
    last, per_pass = {}, []
    while clock['now'] < start + 3 * period:
        calls = p.captured(f)
        per_pass.append(len(calls))
        for s in calls:
            assert s not in last or clock['now'] - last[s] >= period - 1e-6, 'no evidence moved: catch-up only'
            last[s] = clock['now']
        wake = f._runtime_catchup_at
        assert wake is not None and f._runtime_retry_at is None  # its own clock, not the re-probe clock
        clock['now'] = max(clock['now'] + 1, wake)  # the lane wakes on that clock
        if clock['now'] > start + period:
            assert all(clock['now'] - last.get(s, start) <= period + 1 for s in chats), 'every chat within one period'
    assert max(per_pass) <= 2, f'spread across passes, never all at once: {max(per_pass)}'
    total = sum(per_pass)
    assert 2 * len(chats) <= total <= 3 * len(chats) + 1, total  # one per chat per period
    assert feedmod.RUNTIME_CATCHUP_S == 15 * 60


def test_catchup_clock_resets_on_any_capture_and_skips_failing_chats():
    f, p, clock = setup(catchup=900.0)
    p.write(S1=['proc_a'])
    assert p.captured(f) == ['S1']
    assert f._runtime_catchup['S1'] == clock['now'] + 900
    p.write(S1=[])
    clock['now'] += 10
    assert p.captured(f) == ['S1']
    assert f._runtime_catchup['S1'] == clock['now'] + 900  # an evidence capture also settles the catch-up
    p.fail.add('S2')
    clock['now'] = max(f._runtime_catchup['S2'], f._runtime_catchup['S3'])
    assert sorted(p.captured(f) + p.captured(f)) == ['S2', 'S3']  # one chat per pass at this size
    assert f._runtime_catchup['S2'] <= clock['now'], 'a failed catch-up is not settled'
    assert 'S2' in f._runtime_retry  # it retries on its own backoff, not every pass
    assert p.captured(f) == []

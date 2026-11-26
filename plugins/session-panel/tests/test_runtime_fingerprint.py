"""The per-chat runtime fingerprint reads exactly what runtime_inputs reads from processes.json, through core.

`process_fingerprints` attributes entries with core's own `_owner_ids` over core's own `_checkpoint_paths`,
so ownership and file selection cannot drift. Field selection is PROCESS_CHECKPOINT_FIELDS: the drift test
runs the real runtime_inputs (refresh_outcome + process_inputs) over a recording checkpoint and fails if
any processes.json field it reads is missing from that list, or if any other code path reads the file."""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

producer = pytest.importorskip('hermes_cli.process_session_activity')

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    producer._invalidate_cache()
    yield tmp_path
    producer._invalidate_cache()


@pytest.fixture
def inputs():
    return _load('_spf_inputs_fp_test', ROOT / 'inputs.py')


def entry(sid, **kw):
    base = {'session_id': sid, 'pid': 4_000_000, 'pid_scope': 'host', 'host_start_time': 1,
            'command': f'sleep {sid}', 'started_at': 1.0, 'session_key': '', 'parent_session_id': '',
            'owner_task_id': '', 'task_id': 'default', 'writer_pid': 1, 'output_log': '/tmp/x'}
    base.update(kw)
    return base


def write(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries))


def test_each_chat_sees_only_the_entries_core_attributes_to_its_lineage(home, inputs):
    lineages = {'A': ['A', 'A0'], 'B': ['B'], 'C': ['C']}
    a = entry('proc_a', session_key='session:A0')         # via an older lineage member
    b = entry('proc_b', parent_session_id='B')
    write(home / 'processes.json', [a, b])
    first = inputs.process_fingerprints(lineages)
    assert first['A'] and first['B'] and first['C'] is None
    write(home / 'processes.json', [a, b])  # an unchanged rewrite
    assert inputs.process_fingerprints(lineages) == first
    write(home / 'processes.json', [a, dict(b, command='other')])  # B's own read field
    second = inputs.process_fingerprints(lineages)
    assert second['A'] == first['A'] and second['B'] != first['B']
    write(home / 'processes.json', [a, dict(b, command='other', writer_pid=2, output_log='/tmp/y')])
    assert inputs.process_fingerprints(lineages) == second, 'fields core never reads do not make B due'
    write(home / 'processes.json', [a, dict(b, command='other', pid_scope='sandbox', sandbox_key='docker:x')])
    sandbox = inputs.process_fingerprints(lineages)['B']
    assert sandbox and sandbox != second, 'a sandbox-scope entry moves its chat too (core checkpoint status reads it)'
    write(home / 'processes.json', [a, dict(b, command='other')])
    write(home / 'profiles' / 'work' / 'processes.json', [entry('proc_c', task_id='session:C')])
    third = inputs.process_fingerprints(lineages)
    assert third['C'] and third['A'] == first['A'], 'every profile checkpoint core reads'
    (home / 'processes.json').write_text('{torn')
    assert inputs.process_fingerprints(lineages)['A'] is None, 'unreadable reads as no entries, as in core'


def record_checkpoint_reads(monkeypatch):
    """Record every field read from a processes.json entry, and which module read the file, whichever way it
    is read: Path.read_text + json.loads, open() + json.load, and whole-entry copies (keys/items/copy/|)."""
    accessed, readers = set(), set()

    class Recording(dict):
        def get(self, key, *default):
            accessed.add(key)
            return super().get(key, *default)

        def __getitem__(self, key):
            accessed.add(key)
            return super().__getitem__(key)

        def __contains__(self, key):
            accessed.add(key)
            return super().__contains__(key)

        def keys(self):
            accessed.add('*keys*')
            return super().keys()

        def items(self):
            accessed.add('*items*')
            return super().items()

        def values(self):
            accessed.add('*values*')
            return super().values()

        def __iter__(self):
            accessed.add('*iter*')
            return super().__iter__()

        def copy(self):
            accessed.add('*copy*')  # a whole-entry copy reads every field: the fingerprint must then cover all
            return super().copy()

        def __or__(self, other):
            accessed.add('*copy*')
            return super().__or__(other)

    class Checkpoint(str):
        """processes.json text as read: whatever json.loads parses from it is recorded."""

    real_loads, real_read_text = json.loads, Path.read_text

    def recording_loads(text, *a, **kw):
        out = real_loads(text, *a, **kw)
        if isinstance(text, Checkpoint) and isinstance(out, list):
            return [Recording(x) if isinstance(x, dict) else x for x in out]
        return out

    def read_text(self, *a, **kw):
        text = real_read_text(self, *a, **kw)
        if self.name != 'processes.json':
            return text
        readers.add(sys._getframe(1).f_globals.get('__name__'))
        return Checkpoint(text)

    import builtins
    real_open, real_load = builtins.open, json.load

    def opening(file, *a, **kw):
        handle = real_open(file, *a, **kw)
        if isinstance(file, (str, os.PathLike)) and Path(file).name == 'processes.json':
            readers.add(sys._getframe(1).f_globals.get('__name__'))
        return handle

    def loading(fp, *a, **kw):
        out = real_load(fp, *a, **kw)
        if Path(str(getattr(fp, 'name', ''))).name == 'processes.json' and isinstance(out, list):
            return [Recording(x) if isinstance(x, dict) else x for x in out]
        return out

    monkeypatch.setattr(json, 'loads', recording_loads)
    monkeypatch.setattr(json, 'load', loading)
    monkeypatch.setattr(builtins, 'open', opening)
    monkeypatch.setattr(Path, 'read_text', read_text)
    return accessed, readers


def test_runtime_inputs_reads_no_processes_json_field_the_fingerprint_misses(home, inputs, monkeypatch):
    from hermes_state import SessionDB
    api = _load('_spf_api_fp_test', ROOT / 'dashboard' / 'plugin_api.py')
    db = SessionDB(db_path=home / 'state.db')
    db.create_session('S1', 'cli')
    db.close()
    # A live entry (this very process, its real kernel birth), so core reads every field it ever reads.
    from gateway.status import get_process_start_time
    write(home / 'processes.json', [entry('proc_live', pid=os.getpid(), host_start_time=get_process_start_time(os.getpid()),
                                          session_key='session:S1', parent_session_id='S1')])
    accessed, readers = record_checkpoint_reads(monkeypatch)
    r = api.CoreReaders(state_db=home / 'state.db')
    try:
        out = r.runtime_inputs('S1', ['S1'])
    finally:
        r.release()
    assert [p['work_id'] for p in out['processes']] == ['proc_live'], 'the probe saw the live entry'
    assert 'command' in accessed and 'pid' in accessed, 'the recording is not vacuous'
    assert accessed <= set(inputs.PROCESS_CHECKPOINT_FIELDS), sorted(accessed - set(inputs.PROCESS_CHECKPOINT_FIELDS))
    # Every core module that reads the checkpoint on this path. A new one fails here until its
    # fields are reviewed into PROCESS_CHECKPOINT_FIELDS (the recording above covers it already).
    assert 'hermes_cli.process_session_activity' in readers
    assert readers <= {'hermes_cli.process_session_activity', 'hermes_state_process_checkpoint'}, readers


def _session_db(home, *sessions):
    from hermes_state import SessionDB
    db = SessionDB(db_path=home / 'state.db')
    for sid, parent in sessions:
        db.create_session(sid, 'cli', **({'parent_session_id': parent} if parent else {}))
    return db


def test_capture_after_an_exit_does_not_certify_the_warm_pre_exit_cache(home, monkeypatch):
    """The chat is due because its last process exited; refresh_outcome must not read core's 3 s cache."""
    import subprocess
    from gateway.status import get_process_start_time
    api = _load('_spf_api_fp_cache', ROOT / 'dashboard' / 'plugin_api.py')
    _session_db(home, ('S1', None)).close()
    child = subprocess.Popen(['sleep', '60'])
    try:
        write(home / 'processes.json', [entry('proc_gone', pid=child.pid, host_start_time=get_process_start_time(child.pid),
                                              session_key='session:S1', parent_session_id='S1')])
        producer._invalidate_cache()
        assert producer.background_process_counts().get('S1') == 1  # the sidebar warmed the cache
    finally:
        child.kill()
        child.wait()
    write(home / 'processes.json', [])
    r = api.CoreReaders(state_db=home / 'state.db')
    try:
        out = r.runtime_inputs('S1', ['S1'])
    finally:
        r.release()
    assert out['processes'] == []
    assert 'proc_gone' not in json.dumps(out['outcome_json']), 'the outcome still shows the exited process running'


def test_fingerprint_covers_the_compression_lineage_refresh_outcome_reads(home):
    """A process owned by a compression continuation moves the chat whose panel lineage does not name it."""
    api = _load('_spf_api_fp_lineage', ROOT / 'dashboard' / 'plugin_api.py')
    db = _session_db(home, ('S1', None))
    db.end_session('S1', 'compression')
    db.create_session('S2', 'cli', parent_session_id='S1')
    db.close()
    write(home / 'processes.json', [entry('proc_child', parent_session_id='S2')])
    r = api.CoreReaders(state_db=home / 'state.db')
    try:
        prints = r.runtime_fingerprints({'S1': ['S1'], 'X': ['X']})
        assert prints['S1'] and prints['X'] is None
        assert r.runtime_fingerprints({'S1': ['S1'], 'X': ['X']}) == prints  # memo: nothing moved
        write(home / 'processes.json', [entry('proc_child', parent_session_id='S2', command='changed')])
        assert r.runtime_fingerprints({'S1': ['S1'], 'X': ['X']})['S1'] != prints['S1']
    finally:
        r.release()


def test_every_profile_checkpoint_wakes_the_lane(home):
    api = _load('_spf_api_fp_watch', ROOT / 'dashboard' / 'plugin_api.py')
    write(home / 'profiles' / 'work' / 'processes.json', [])
    r = api.CoreReaders(state_db=home / 'state.db')
    try:
        watched = {str(p) for p in r.watch_paths()}
    finally:
        r.release()
    assert str(home / 'processes.json') in watched
    assert str(home / 'profiles' / 'work' / 'processes.json') in watched


def test_the_drift_recorder_sees_open_json_load_and_copy_readers(home, monkeypatch):
    """The drift test is only as good as its recorder: a reader via open()/json.load or a dict copy is caught."""
    write(home / 'processes.json', [entry('proc_a')])
    accessed, readers = record_checkpoint_reads(monkeypatch)
    with open(home / 'processes.json', encoding='utf-8') as fh:
        rows = json.load(fh)
    assert rows[0].get('writer_pid') == 1 and 'writer_pid' in accessed
    assert __name__ in readers
    dict(rows[0].copy())
    assert '*copy*' in accessed

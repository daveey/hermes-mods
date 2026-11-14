#!/usr/bin/env python3
"""Offline SQLite installation with WAL-aware staging and fail-closed cutover.

No process is killed. Drain/stop every opener before use. Stopped units stay stopped
on failure; a partial cutover requires operator reconciliation from its receipt.
See README.md for recovered state policy, ownership and the SSH serve runbook.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SIDECARS = ("-wal", "-shm", "-journal")
# hermes_cli.session_recovery._RUNTIME_TABLES: exact policy, never a prefix match.
RUNTIME_TABLES = frozenset({"runtime_epoch", "session_admissions", "worker_executions", "worker_receipts"})
DERIVED_TABLES = frozenset({
    'messages_fts', 'messages_fts_data', 'messages_fts_idx', 'messages_fts_docsize', 'messages_fts_config',
    'messages_fts_trigram', 'messages_fts_trigram_data', 'messages_fts_trigram_idx',
    'messages_fts_trigram_docsize', 'messages_fts_trigram_config',
})
# hermes_state_session_change: a derived per-session change cursor. Every current writer (re)installs
# it with a rotated install epoch, after which readers distrust any older cursor and do a full read;
# recovery rebuilds it from the copied sessions, so its rows and epoch legitimately differ.
CURSOR_TABLES = frozenset({'session_change', 'session_change_revision'})
GENERATED_META_KEYS = frozenset({
    'fts_storage_version', 'fts_optimize_available', 'fts_rebuild_high_water', 'fts_rebuild_progress',
    'fts_cjk_stale', 'fts_cjk_rebuild_high_water', 'fts_cjk_rebuild_progress',
    'telegram_dm_topic_schema_version', 'fts_tool_full_content_high_water',
})  # hermes_cli.session_recovery._GENERATED_META_KEYS; no prefix exemptions



class Refusal(RuntimeError):
    pass


def _proc_start(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _proc_entries():
    """Every /proc entry; holders() scans them all. Tests narrow this seam to their own tree."""
    return Path('/proc').iterdir()


def holders(db: Path, extra_paths=()):
    """Linux local PID namespace only. Incomplete inspection is NOT zero holders."""
    if not Path('/proc/self/fd').is_dir():
        raise Refusal('holder inspection requires Linux /proc; no unsupported-platform bypass')
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        if len(fields) > 5 and fields[4] == '/proc' and any(
                key in line for key in ('hidepid=', 'subset=')):
            raise Refusal('restricted /proc mount cannot prove zero holders')
    for line in Path('/proc/self/status').read_text().splitlines():
        if line.startswith('NSpid:') and len(line.split()) > 2:
            raise Refusal('nested PID namespace cannot prove zero holders')
    names = {str(path) + s for path in (db, *extra_paths) for s in ("",) + SIDECARS}
    inodes = set()
    for name in names:
        try:
            st = os.stat(name)
            inodes.add((st.st_dev, st.st_ino))
        except FileNotFoundError:
            pass
    out = []
    for proc in _proc_entries():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        pid = int(proc.name)
        birth = _proc_start(pid)
        try:
            matches = []
            for fd in (proc / 'fd').iterdir():
                try:
                    link = os.readlink(fd)
                    st = fd.stat()
                except FileNotFoundError:
                    continue
                if link.removesuffix(' (deleted)') in names or (st.st_dev, st.st_ino) in inodes:
                    matches.append(link)
            # mmap can outlive the FD (notably shared memory sidecars).
            for line in (proc / 'maps').read_text().splitlines():
                fields = line.split(None, 5)
                if len(fields) < 5:
                    continue
                major, minor = (int(v, 16) for v in fields[3].split(':'))
                inode = (os.makedev(major, minor), int(fields[4]))
                link = fields[5] if len(fields) == 6 else ''
                if inode in inodes or link.removesuffix(' (deleted)') in names:
                    matches.append(link)
            if matches:
                cmd = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')[:300]
                out.append((pid, matches[0], f'birth={birth} {cmd}'))
        except (FileNotFoundError, ProcessLookupError):
            continue  # exited during the scan
        except PermissionError as exc:
            # A zombie has no descriptors or mappings. Everything else is unknown.
            try:
                zombie = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z'
            except FileNotFoundError:
                continue
            if not zombie:
                raise Refusal(f'incomplete holder scan at pid {pid}: {exc}; use --holder-scan-sudo '
                              'for a privileged read-only scan, or run from a fully visible host namespace') from exc
    return out


def _scan(db, privileged=False, extra_paths=()):
    if not privileged:
        return holders(db, extra_paths)
    extra_args = [arg for path in extra_paths for arg in ('--scan-extra', str(path))]
    result = subprocess.run(['sudo', '-n', sys.executable, str(Path(__file__).resolve()),
                             '--scan-holders', str(db), *extra_args], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise Refusal('privileged holder scan failed: ' + result.stderr.strip())
    return json.loads(result.stdout)


def _no_holders(db, privileged=False, extra_paths=()):
    held = _scan(db, privileged, extra_paths)
    if held:
        detail = '\n'.join(f'  pid {pid} {link} {cmd}' for pid, link, cmd in held)
        raise Refusal('live file or sidecar is still open:\n' + detail + '\nDrain turns using supported '
                      'pause-turns, then close the identified Desktop SSH connection or stop its exact '
                      'owning supervisor; pausing alone does not close FDs. Do not kill SSH shells or '
                      'use serve --stop. Re-run after all holders exit. See README.md.')


def _ro_connect(path, timeout=5, allow_fallback=True):
    """WAL-aware reader; fallback is safe only for our private offline source copy."""
    uri = Path(path).resolve().as_uri()
    con = None
    try:
        con = sqlite3.connect(f'{uri}?mode=ro', uri=True, timeout=timeout)
        con.execute('select 1 from sqlite_master limit 1')
        return con
    except sqlite3.OperationalError:
        if con is not None:
            con.close()
        if not allow_fallback:
            raise
        con = sqlite3.connect(f'{uri}?mode=rw', uri=True, timeout=timeout)
        con.execute('PRAGMA query_only=ON')
        return con


def _immutable(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?immutable=1', uri=True)


def _validate(path):
    with contextlib.closing(_immutable(path)) as con:
        rows = list(con.execute('PRAGMA integrity_check'))
        if rows != [('ok',)]:
            raise Refusal(f'replacement integrity_check: {rows[:3]}')
        rows = list(con.execute('PRAGMA foreign_key_check'))
        if rows:
            raise Refusal(f'replacement foreign_key_check: {rows[:3]}')


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _row_hashes(con, table):
    # Multiset preserves duplicate rows and compares blobs without serializing PII to receipts.
    return sorted(hashlib.sha256(repr(row).encode()).hexdigest()
                  for row in con.execute('SELECT * FROM ' + _quote(table)))


def _policy(source, staged, recovered, ack):
    """No unreadable-schema bypass, and no empty-table bypass for durable/security data."""
    with contextlib.closing(_ro_connect(source)) as old, contextlib.closing(_immutable(staged)) as new:
        want, have = _tables(old), _tables(new)
        missing = sorted(want - have)
        allowed = RUNTIME_TABLES | CURSOR_TABLES if recovered else frozenset()
        forbidden = sorted(set(missing) - allowed)
        if forbidden:
            raise Refusal(f'replacement lacks durable/unknown tables: {forbidden}')
        inventory = {}
        for table in sorted(want):
            before = old.execute('SELECT count(*) FROM ' + _quote(table)).fetchone()[0]
            after = new.execute('SELECT count(*) FROM ' + _quote(table)).fetchone()[0] if table in have else 0
            inventory[table] = {'source_rows': before, 'staged_rows': after}
            if recovered and table == 'runtime_epoch':
                continue  # preserved separately as a monotonic floor, never reset to epoch 1
            if recovered and table in CURSOR_TABLES:
                continue  # derived cursor: reinstalled with a new epoch, so readers fall back to a full read
            if after < before and table != 'sqlite_sequence':
                raise Refusal(f'durable/idempotency row loss in {table}: {before} -> {after}')
            # Preserve transcript/security/revocation/idempotency content exactly;
            # equal row counts alone do not prove retention.
            if recovered and table not in {'state_meta', 'sqlite_sequence'} | DERIVED_TABLES:
                if before and (table not in have or _row_hashes(old, table) != _row_hashes(new, table)):
                    raise Refusal(f'durable/idempotency content changed in {table}')
            if recovered and table == 'state_meta':
                before_meta = {k: v for k, v in old.execute('SELECT key,value FROM state_meta')
                               if k not in GENERATED_META_KEYS}
                after_meta = dict(new.execute('SELECT key,value FROM state_meta'))
                if any(k not in after_meta or after_meta[k] != v for k, v in before_meta.items()):
                    raise Refusal('durable state_meta values changed')
        if missing and not ack:
            raise Refusal(f'allowed runtime omissions {missing}; review inventory and pass --ack-runtime-loss')
        epoch = 0
        if recovered and 'runtime_epoch' in want:
            row = old.execute('SELECT epoch FROM runtime_epoch WHERE singleton=1').fetchone()
            if row:
                epoch = row[0]
                if not isinstance(epoch, int) or not 0 < epoch < 9223372036854775807:
                    raise Refusal('cannot prove a safe runtime epoch floor')
        sequences = dict(old.execute('SELECT name,seq FROM sqlite_sequence')) if 'sqlite_sequence' in want else {}
        return {'omitted_runtime_tables': missing, 'tables': inventory, 'runtime_epoch_floor': epoch,
                'sequence_floors': sequences}


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _save(path, data):
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as out:
        os.chmod(tmp, 0o600)
        json.dump(data, out, indent=2)
        out.write('\n')
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _fingerprint(db):
    result = {}
    for suffix in ('',) + SIDECARS:
        path = Path(str(db) + suffix)
        if path.exists():
            info = path.stat()
            with open(path, 'rb') as inp:
                hasher = hashlib.sha256()
                for block in iter(lambda: inp.read(1024 * 1024), b''):
                    hasher.update(block)
                digest = hasher.hexdigest()
            result[suffix] = {'device': info.st_dev, 'inode': info.st_ino, 'size': info.st_size,
                              'mtime_ns': info.st_mtime_ns, 'sha256': digest}
    return result


def _safe_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
        raise Refusal(f'expected own regular single-link file, not symlink/alias: {path}')
    return info


def _snapshot(replacement, staged):
    # Never immutable: committed source WAL belongs in the replacement snapshot.
    # No rw fallback against the operator's input; unsupported SQLite readers refuse.
    with contextlib.closing(_ro_connect(replacement, allow_fallback=False)) as src:
        with contextlib.closing(sqlite3.connect(staged)) as dst:
            deadline = time.monotonic() + 60
            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise Refusal('replacement backup snapshot timed out')
            src.backup(dst, pages=256, progress=progress)
            dst.execute('PRAGMA journal_mode=DELETE').fetchone()
    os.chmod(staged, 0o600)
    _validate(staged)


def systemctl(action, unit):
    subprocess.run(['systemctl', '--user', action, '--', unit], check=True, timeout=120)


def _maintenance(db):
    try:
        from gateway.runtime_ownership import exclusive_maintenance
    except ImportError as exc:
        raise Refusal('state.db installation requires the installed Hermes core environment '
                      '(gateway.runtime_ownership.exclusive_maintenance); use its managed Python') from exc
    return exclusive_maintenance([db.parent], purpose='offline-state-install')


@contextlib.contextmanager
def _recovery_record(db):
    path = db.with_name(db.name + '.recover.pid')
    _save(path, {'pid': os.getpid(), 'proc_start': _proc_start(os.getpid()),
                 'session_id': os.environ.get('HERMES_SESSION_ID'),
                 'task_id': os.environ.get('HERMES_RECOVERY_HOLDER_TASK') or os.environ.get('HERMES_KANBAN_TASK')})
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def swap(db, replacement, units, wait_s=10.0, allow_kanban=False,
         recovered_state=False, ack_runtime_loss=False, holder_scan_sudo=False):
    db, replacement = db.expanduser().absolute(), replacement.expanduser().absolute()
    work = None
    receipt = {'phase': 'preflight', 'db': str(db), 'replacement': str(replacement),
               'pid': os.getpid(), 'birth': _proc_start(os.getpid()), 'stopped_units': [], 'started_units': [], 'moves': []}
    try:
        pending = db.with_name(db.name + '.swap-pending.json')
        if pending.exists():
            raise Refusal(f'unresolved cutover: inspect {pending}; reconcile its receipt before retrying')
        _safe_file(db)
        _safe_file(replacement)
        db, replacement = db.resolve(), replacement.resolve()
        if db == replacement or str(replacement) in {str(db) + s for s in SIDECARS}:
            raise Refusal('replacement must be separate from target and sidecars')
        if db.name == 'kanban.db' and not allow_kanban:
            raise Refusal('Kanban board: prefer hermes kanban recover; --allow-kanban requires offline ownership')
        if recovered_state and db.name != 'state.db':
            raise Refusal('--recovered-state is only for state.db')
        if db.name == 'state.db' and not recovered_state:
            raise Refusal('state.db requires --recovered-state (epoch and idempotency preservation)')
        if ack_runtime_loss and not recovered_state:
            raise Refusal('--ack-runtime-loss requires --recovered-state')
        pending = db.with_name(db.name + '.swap-pending.json')
        lock_path = db.with_name(db.name + '.recovery.lock')
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a+b') as lock, contextlib.ExitStack() as contexts:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refusal(f'another recovery holds {lock_path}; one owner at a time') from None
            contexts.enter_context(_recovery_record(db))
            if pending.exists():
                raise Refusal(f'unresolved cutover: inspect {pending}; reconcile its receipt before retrying')
            work = Path(tempfile.mkdtemp(prefix=f'.{db.name}.recovery-', dir=db.parent))
            staged, source, archive = work / 'staged.db', work / 'source.db', work / 'original.db'
            receipt.update(stage=str(staged), archive=str(archive), receipt=str(work / 'receipt.json'))
            _save(work / 'receipt.json', receipt)
            _snapshot(replacement, staged)
            for unit in dict.fromkeys(units):
                receipt['stop_attempt'] = unit
                _save(work / 'receipt.json', receipt)
                systemctl('stop', unit)
                receipt['stopped_units'].append(unit)
                _save(work / 'receipt.json', receipt)
            deadline = time.monotonic() + max(0, wait_s)
            while _scan(db, holder_scan_sudo) and time.monotonic() < deadline:
                time.sleep(.25)
            _no_holders(db, holder_scan_sudo)
            maintenance = _maintenance(db) if db.name == 'state.db' else contextlib.nullcontext()
            with maintenance:
                _no_holders(db, holder_scan_sudo)
                baseline = _fingerprint(db)
                for suffix in ('',) + SIDECARS:
                    original = Path(str(db) + suffix)
                    if original.exists():
                        _safe_file(original)
                        shutil.copyfile(original, Path(str(source) + suffix))
                receipt['inventory'] = _policy(source, staged, recovered_state, ack_runtime_loss)
                print('inventory: ' + json.dumps(receipt['inventory'], sort_keys=True), flush=True)
                floor = receipt['inventory']['runtime_epoch_floor']
                if recovered_state and floor:
                    with contextlib.closing(sqlite3.connect(staged)) as con:
                        con.execute('''CREATE TABLE IF NOT EXISTS runtime_epoch (
                            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                            epoch INTEGER NOT NULL CHECK(epoch>0), instance_id TEXT NOT NULL)''')
                        con.execute('''INSERT INTO runtime_epoch VALUES(1,?, 'offline-recovery')
                            ON CONFLICT(singleton) DO UPDATE SET epoch=max(epoch,excluded.epoch),
                            instance_id=excluded.instance_id''', (floor,))
                        con.commit()
                sequences = receipt['inventory']['sequence_floors']
                if sequences:
                    with contextlib.closing(sqlite3.connect(staged)) as con:
                        for name, seq in sequences.items():
                            current = con.execute('SELECT seq FROM sqlite_sequence WHERE name=?', (name,)).fetchone()
                            if current is None:
                                con.execute('INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)', (name, seq))
                            elif current[0] < seq:
                                con.execute('UPDATE sqlite_sequence SET seq=? WHERE name=?', (seq, name))
                        con.commit()
                _validate(staged)
                with open(staged, 'rb') as inp:
                    os.fsync(inp.fileno())
                receipt.update(phase='prepared', source_fingerprint=baseline,
                               stage_fingerprint=_fingerprint(staged))
                _save(work / 'receipt.json', receipt)
                _no_holders(db, holder_scan_sudo)
                if _fingerprint(db) != baseline:
                    raise Refusal('source changed during offline inventory; drain all producers and retry')
                # Pending marker is durable BEFORE the first move; crash/retry cannot silently
                # install over a half-moved original. Never auto-rollback a corrupt database.
                _save(pending, {'receipt': str(work / 'receipt.json')})
                # Main stays present across crashes; archive its inode before atomic publication.
                receipt.update(phase='archiving', next_move=[str(db), str(archive)])
                _save(work / 'receipt.json', receipt)
                os.link(db, archive)
                _fsync_dir(work)
                receipt['moves'].append([str(db), str(archive), 'hardlink'])
                _save(work / 'receipt.json', receipt)
                for suffix in SIDECARS:
                    original, dest = Path(str(db) + suffix), Path(str(archive) + suffix)
                    if not original.exists():
                        continue
                    _no_holders(db, holder_scan_sudo, (archive,))
                    receipt.update(phase='moving', next_move=[str(original), str(dest)])
                    _save(work / 'receipt.json', receipt)
                    os.rename(original, dest)
                    _fsync_dir(db.parent)
                    _fsync_dir(work)
                    receipt['moves'].append([str(original), str(dest)])
                    _save(work / 'receipt.json', receipt)
                _no_holders(db, holder_scan_sudo, (archive,))
                if any(Path(str(db) + s).exists() for s in SIDECARS):
                    raise Refusal('target bundle reappeared during cutover; originals retained, no restart')
                if not db.exists() or db.stat().st_ino != archive.stat().st_ino:
                    raise Refusal('canonical main changed during cutover; no restart')
                receipt.update(phase='installing', next_move=[str(staged), str(db)])
                _save(work / 'receipt.json', receipt)
                os.rename(staged, db)
                _fsync_dir(db.parent)
                _fsync_dir(work)
                _validate(db)
                _no_holders(db, holder_scan_sudo, (archive,))
                receipt.update(phase='verified', installed_fingerprint=_fingerprint(db))
                _save(work / 'receipt.json', receipt)
                pending.unlink()
                _fsync_dir(db.parent)
            # Core reservation must be released before a successful service start.
            for unit in reversed(receipt['stopped_units']):
                systemctl('start', unit)
                receipt['started_units'].append(unit)
                _save(work / 'receipt.json', receipt)
            receipt['phase'] = 'complete'
            _save(work / 'receipt.json', receipt)
            print(f'swapped: {db}; receipt: {work / "receipt.json"}; originals: {archive}*')
            return 0
    except (OSError, RuntimeError, sqlite3.DatabaseError, subprocess.SubprocessError, ValueError) as exc:
        receipt['error'] = str(exc)
        receipt['failed_phase'] = receipt['phase']
        receipt['phase'] = 'aborted'
        if work is not None:
            with contextlib.suppress(OSError):
                _save(work / 'receipt.json', receipt)
        print(f'ABORT: {exc}\nNo automatic rollback/restart on failed cutover. '
              f'Started after verification: {receipt["started_units"]}. '
              f'Receipt: {work / "receipt.json" if work else "preflight only"}', file=sys.stderr)
        return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--db', type=Path)
    ap.add_argument('--replacement', type=Path)
    ap.add_argument('--stop-unit', action='append', default=[])
    ap.add_argument('--wait', type=float, default=10)
    ap.add_argument('--allow-kanban', action='store_true')
    ap.add_argument('--recovered-state', action='store_true')
    ap.add_argument('--ack-runtime-loss', action='store_true', help='acknowledge reported exact-name empty runtime omissions')
    ap.add_argument('--holder-scan-sudo', action='store_true', help='sudo -n for read-only /proc scanner only')
    ap.add_argument('--scan-holders', type=Path, help=argparse.SUPPRESS)
    ap.add_argument('--scan-extra', type=Path, action='append', default=[], help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.scan_holders is not None:
        try:
            print(json.dumps(holders(a.scan_holders.resolve(), a.scan_extra)))
            return 0
        except (OSError, Refusal) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    if not a.db or not a.replacement:
        ap.error('--db and --replacement are required')
    return swap(a.db, a.replacement, a.stop_unit, a.wait, a.allow_kanban,
                a.recovered_state, a.ack_runtime_loss, a.holder_scan_sudo)


if __name__ == '__main__':
    sys.exit(main())

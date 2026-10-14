"""Local-only many-to-many ownership registry; never modifies cloud resources."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3


def identity_value(value):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError('identity fields must be nonempty strings without control characters')
    return value


def validate_key(key):
    try:
        parts = json.loads(key)
    except (TypeError, ValueError) as exc:
        raise ValueError('machine_key must be a JSON array of four strings') from exc
    if not isinstance(parts, list) or len(parts) != 4:
        raise ValueError('machine_key must be a JSON array of four strings')
    for part in parts:
        identity_value(part)
    canonical = json.dumps(parts, ensure_ascii=False, separators=(',', ':'))
    if key != canonical:
        raise ValueError('machine_key must use canonical compact JSON encoding')
    return key


class OwnershipStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS bindings (machine_key TEXT NOT NULL, gateway_id TEXT NOT NULL, profile TEXT NOT NULL, session_id TEXT NOT NULL, source TEXT NOT NULL DEFAULT \'manual\', PRIMARY KEY(machine_key,gateway_id,profile,session_id))')
            # 'source' (manual|auto) distinguishes explicit links from evidence
            # auto-links. Its presence also makes the legacy 4-value INSERT
            # (blanket auto-link in pre-fix code) fail instead of polluting.
            if 'source' not in [r[1] for r in db.execute('PRAGMA table_info(bindings)')]:
                db.execute("ALTER TABLE bindings ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")

    def connect(self, timeout=30):
        return sqlite3.connect(self.path, timeout=timeout)

    def _values(self, key, gateway_id, profile, session_id):
        return (validate_key(key), *(identity_value(v) for v in (gateway_id, profile, session_id)))

    def attach(self, machine_key, gateway_id, profile, session_id, source='manual'):
        values = self._values(machine_key, gateway_id, profile, session_id)
        if source not in ('manual', 'auto'):
            raise ValueError('source must be manual or auto')
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO bindings (machine_key,gateway_id,profile,session_id,source) VALUES (?,?,?,?,?)', (*values, source))
        return self.owners(machine_key)

    def detach(self, machine_key, gateway_id, profile, session_id):
        values = self._values(machine_key, gateway_id, profile, session_id)
        with self.connect() as db:
            db.execute('DELETE FROM bindings WHERE machine_key=? AND gateway_id=? AND profile=? AND session_id=?', values)
        return self.owners(machine_key)

    def owners(self, machine_key):
        validate_key(machine_key)
        with self.connect() as db:
            rows = db.execute('SELECT gateway_id,profile,session_id FROM bindings WHERE machine_key=? ORDER BY gateway_id,profile,session_id', (machine_key,)).fetchall()
        return [dict(zip(('gateway_id','profile','session_id'), row)) for row in rows]

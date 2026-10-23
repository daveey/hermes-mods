"""A stand-in for hermes_hub.py with the pieces hub-sync uses (schema, open_db, queue_request, send_queued, Api).

The schema and the queue/send semantics are copied from the real client (hermes-hub/client/hermes_hub.py, 1.0.0);
``test_real_client.py`` runs the same flows against the real client when it is installed.
Tests flip ``HUB_UP`` and read ``POSTED`` to simulate the hub.
"""
import json
import sqlite3
import time
import uuid

VERSION = "fake-1"
HUB_UP = False
POSTED = []

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS published (coll TEXT, key TEXT, sha TEXT, data TEXT, version INTEGER, seq INTEGER,
  PRIMARY KEY (coll, key));
CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, op_id TEXT UNIQUE, coll TEXT, key TEXT,
  data TEXT, deleted INTEGER, created_at REAL, UNIQUE (coll, key));
CREATE TABLE IF NOT EXISTS replica (gw TEXT, coll TEXT, key TEXT, version INTEGER, seq INTEGER, data TEXT,
  updated_at INTEGER, PRIMARY KEY (gw, coll, key));
CREATE TABLE IF NOT EXISTS req_ledger (id TEXT PRIMARY KEY, state TEXT, result TEXT, at REAL);
CREATE TABLE IF NOT EXISTS sent (id TEXT PRIMARY KEY, body TEXT, state TEXT, result TEXT, created_at REAL,
  done_at REAL, posted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS deps (board TEXT, task TEXT, waits_on TEXT, created_at REAL, PRIMARY KEY (board, task));
"""


class Offline(Exception):
    pass


def load_config():
    return {"hub_url": "https://hub.example.com", "gateway": "laptop"}


def read_key():
    return "test-key"


class Api:
    def __init__(self, url, key):
        self.url = url

    def call(self, method, path, body=None):
        if not HUB_UP:
            raise Offline("network: down (test)")
        POSTED.append((method, path, body))
        return 201, {"ok": True}


def open_db(path=None):
    db = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript(SCHEMA)
    return db


def queue_request(db, to_gw, coll, key, action, args=None, base_version=None, rid=None):
    rid = rid or "hr-" + uuid.uuid4().hex
    body = {"id": rid, "to_gw": to_gw, "coll": coll, "key": key, "action": action, "args": args or {}}
    if base_version is not None:
        body["base_version"] = base_version
    db.execute("INSERT INTO sent (id, body, state, created_at) VALUES (?, ?, 'local', ?)", (rid, json.dumps(body), time.time()))
    return rid


def send_queued(db, api):
    n = 0
    for r in db.execute("SELECT id, body FROM sent WHERE state = 'local' ORDER BY created_at").fetchall():
        st, js = api.call("POST", "/v2/sync/requests", json.loads(r["body"]))
        if st in (200, 201):
            db.execute("UPDATE sent SET state = 'queued' WHERE id = ?", (r["id"],))
            n += 1
    return n

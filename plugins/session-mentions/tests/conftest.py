"""Test processes never start the dashboard's outbox watcher thread (tests call watch_once directly)."""
import os

os.environ.setdefault("HERMES_SESSION_MENTIONS_OUTBOX_WATCH", "0")

"""Suite safety fences installed before test-module collection."""
import _sqlite3
import sqlite3
import os
import subprocess

import pytest


def _deny_sql(*args, **kwargs):
    raise AssertionError('service-owner-guard fixtures must not access SQLite')


sqlite3.connect = _sqlite3.connect = _deny_sql


@pytest.fixture(autouse=True)
def no_process_signals(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('owner-guard tests inspect decisions, never send process signals')
    monkeypatch.setattr(os, 'kill', deny)
    monkeypatch.setattr(subprocess.Popen, 'send_signal', deny)

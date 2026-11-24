"""Measured CPython boundary costs, never inferred no-I/O from construction."""
import sqlite3
import sys

import pytest

from test_feed import feedmod


def probe():
    return feedmod.Feed._source_module().StageCost()


def test_stage_cost_counts_real_sqlite_and_quiet_intervals():
    cost = probe()
    cost.start()
    conn = sqlite3.connect(':memory:')
    try:
        assert conn.execute('select 1').fetchall() == [(1,)]
    finally:
        conn.close()
    measured = cost.finish()
    assert measured['available'] and measured['db_opens'] == 1
    assert measured['sql_calls'] == 1 and measured['network_calls'] == 0
    quiet = probe(); quiet.start()
    assert quiet.finish() == dict(available=True, db_opens=0, sql_calls=0, network_calls=0)
    assert sys.getprofile() is None


def test_stage_cost_does_not_replace_preexisting_profiler():
    calls = []
    def profiler(frame, event, arg):
        calls.append(event)
    previous = sys.getprofile()
    sys.setprofile(profiler)
    try:
        cost = probe(); cost.start()
        result = cost.finish()
        assert result == dict(available=False, reason='preexisting_profiler')
        assert sys.getprofile() is profiler and calls
    finally:
        sys.setprofile(previous)


def test_stage_cost_counts_real_local_socket_io_without_external_network():
    import socket
    first, second = socket.socketpair()
    cost = probe(); cost.start()
    try:
        assert first.send(b'x') == 1
        assert second.recv(1) == b'x'
    finally:
        measured = cost.finish()
        first.close(); second.close()
    assert measured == dict(available=True, db_opens=0, sql_calls=0, network_calls=2)


def test_stage_cost_marks_replaced_probe_unavailable_without_clobbering_successor():
    previous = sys.getprofile()
    def successor(frame, event, arg):
        pass
    cost = probe(); cost.start()
    sys.setprofile(successor)
    try:
        assert cost.finish() == dict(available=False, reason='profiler_replaced')
        assert sys.getprofile() is successor
    finally:
        sys.setprofile(previous)

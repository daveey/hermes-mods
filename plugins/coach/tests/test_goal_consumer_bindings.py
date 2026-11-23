"""Fixture-local overrides must survive core's split notification helpers."""
import types

import pytest

from goal_consumer_fixture import runtime_globals


def _outer():
    return _split_helper()


def _helper():
    return _sink()


def test_split_server_helper_uses_fixture_override():
    pytest.importorskip('tui_gateway.method_ctx')
    server = types.ModuleType('fixture_server')
    module = types.ModuleType('fixture_notifications')
    server._sink = lambda: 'production'
    server._split_helper = types.FunctionType(_helper.__code__, vars(server))
    module._outer = types.FunctionType(_outer.__code__, vars(module))
    g = runtime_globals(server, module)
    g['_sink'] = lambda: 'fixture'
    assert g['_outer']() == 'fixture'
    assert server._split_helper() == 'production'

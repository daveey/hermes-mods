"""The contract test's Node picker skips a too-old first-on-PATH node and fails clearly when none qualifies."""
import os
from pathlib import Path

import pytest

from node_pick import NodeNotFound, find_node, node_candidates


def fake_node(directory, version, body=None):
    directory.mkdir(parents=True, exist_ok=True)
    node = directory / 'node'
    node.write_text(body or f'#!/bin/sh\necho {version}\n')
    node.chmod(0o755)
    return node


def pick(tmp_path, entries, home='home', homebrew='absent/node'):
    return find_node(
        path_env=os.pathsep.join(str(entry) for entry in entries),
        home=tmp_path / home, homebrew=tmp_path / homebrew,
    )


def test_old_node_first_on_path_is_skipped_for_a_later_qualifying_one(tmp_path):
    old = fake_node(tmp_path / 'old', 'v18.15.0')
    new = fake_node(tmp_path / 'new', 'v26.5.1')
    assert node_candidates(f'{old.parent}{os.pathsep}{new.parent}', tmp_path / 'home', tmp_path / 'x') == [old, new]
    assert pick(tmp_path, [old.parent, tmp_path / 'missing', new.parent]) == str(new)


def test_first_qualifying_path_node_wins_and_minimum_is_inclusive(tmp_path):
    below = fake_node(tmp_path / 'below', 'v18.18.2')
    exact = fake_node(tmp_path / 'exact', 'v18.19.0')
    newer = fake_node(tmp_path / 'newer', 'v22.1.0')
    assert pick(tmp_path, [below.parent, exact.parent, newer.parent]) == str(exact)


def test_falls_back_to_newest_hermes_tool_then_homebrew(tmp_path):
    old = fake_node(tmp_path / 'old', 'v18.15.0')
    tools = tmp_path / 'home' / '.hermes' / 'tools'
    fake_node(tools / 'node-9.11.0-darwin-arm64' / 'bin', 'v9.11.0')
    fake_node(tools / 'node-20.1.0-darwin-arm64' / 'bin', 'v20.1.0')
    newest = fake_node(tools / 'node-26.12.1-darwin-arm64' / 'bin', 'v26.12.1')
    brew = fake_node(tmp_path / 'brew', 'v24.0.0')
    assert pick(tmp_path, [old.parent], homebrew='brew/node') == str(newest)
    assert pick(tmp_path, [old.parent], home='empty-home', homebrew='brew/node') == str(brew)


def test_no_qualifying_node_fails_listing_each_candidate_and_the_requirement(tmp_path):
    old = fake_node(tmp_path / 'old', 'v18.15.0')
    broken = fake_node(tmp_path / 'broken', '', body='#!/bin/sh\necho boom >&2\nexit 3\n')
    tool = fake_node(tmp_path / 'home' / '.hermes' / 'tools' / 'node-16.20.2-linux-x64' / 'bin', 'v16.20.2')
    with pytest.raises(NodeNotFound) as raised:
        pick(tmp_path, [old.parent, broken.parent])
    message = str(raised.value)
    assert isinstance(raised.value, AssertionError)  # a failure, never a skip
    assert 'Node >= 18.19 is required' in message
    assert f'{old}: v18.15.0' in message
    assert f'{broken}: unusable (exit 3: boom)' in message
    assert f'{tool}: v16.20.2' in message


def test_no_node_at_all_fails_rather_than_skips(tmp_path):
    with pytest.raises(NodeNotFound, match=r'Node >= 18\.19 is required[\s\S]*no node executable found'):
        pick(tmp_path, [tmp_path / 'missing'])


def test_contract_test_uses_the_picker_not_the_first_path_node():
    source = Path(__file__).with_name('test_desktop_contract.py').read_text()
    assert 'find_node()' in source
    assert 'shutil.which' not in source

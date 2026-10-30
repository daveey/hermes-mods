"""Scan authoritative source, not nested stale agent worktrees."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('scan_nested', Path(__file__).resolve().parents[1] / 'poster_scan.py')
assert SPEC is not None and SPEC.loader is not None
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)


@pytest.mark.parametrize('nested', ['.claude/worktrees/old-branch', '.worktrees/old-branch', 'subrepo/.claude/worktrees/old-branch', 'subrepo/.worktrees/old-branch'])
def test_nested_worktree_copies_are_not_live_posters(tmp_path, nested):
    stale = tmp_path / nested / 'client.py'
    stale.parent.mkdir(parents=True)
    stale.write_text("inbox.post('stale', poster='stale-client')\n")
    live = tmp_path / 'client.py'
    live.write_text("inbox.post('live', poster='unregistered-live-client')\n")
    assert scanner.scan(tmp_path) == [{'site': 'client.py:1', 'poster': 'unregistered-live-client'}]


def test_non_worktree_claude_sources_remain_scanned(tmp_path):
    source = tmp_path / '.claude/hooks/notify.py'
    source.parent.mkdir(parents=True)
    source.write_text("inbox.post('live', poster='hook-client')\n")
    assert [hit['poster'] for hit in scanner.scan(tmp_path)] == ['hook-client']

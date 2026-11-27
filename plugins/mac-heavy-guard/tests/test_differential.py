"""Differential gate: the head must hold everything origin/main holds (zero new misses), every reviewer
repro must be held, and any deliberate new allow must be listed with a reason. Strings only; nothing runs."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diff_corpus  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'main_verdicts.json').read_text())

# Deliberate new allows: command -> reason (head allows what main held). Keep empty unless justified.
NEW_ALLOWS: dict = {}


@pytest.fixture(scope='module')
def head():
    os.environ.pop('MAC_HEAVY_OK', None)
    return diff_corpus.verdicts(ROOT, diff_corpus.build())


def test_fixture_matches_corpus():
    assert set(FIXTURE) == set(diff_corpus.build()), 'regenerate fixtures/main_verdicts.json against origin/main'


def test_zero_new_misses_against_main(head):
    missed = [(c, i) for c, (a, b) in FIXTURE.items() for i, (m, h) in enumerate(zip((a, b), head[c]))
              if m and not h and NEW_ALLOWS.get(c) is None]
    assert not missed, f'{len(missed)} commands main holds but head allows, e.g. {missed[:5]}'


def test_new_allows_are_listed_with_reasons(head):
    for c, reason in NEW_ALLOWS.items():
        assert reason.strip() and c in FIXTURE


@pytest.mark.parametrize('command', diff_corpus.REPROS)
def test_every_review_repro_is_held(command, head):
    # Held by at least one channel (local guard or tailnet); and wherever main holds a channel, so does the head.
    assert any(head[command]), 'neither local guard nor tailnet holds ' + repr(command)
    for main, now in zip(FIXTURE[command], head[command]):
        assert now or not main


def test_corpus_counts(head, capsys):
    total = len(FIXTURE)
    main_held = sum(1 for v in FIXTURE.values() if v[0] or v[1])
    head_held = sum(1 for v in head.values() if v[0] or v[1])
    new_allowed = sum(1 for c, v in FIXTURE.items() if (v[0] and not head[c][0]) or (v[1] and not head[c][1]))
    print(f'CORPUS total={total} main_held={main_held} head_held={head_held} new_allowed={new_allowed}')
    assert new_allowed == len(NEW_ALLOWS)

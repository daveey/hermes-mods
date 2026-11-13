"""Close-outs show comments posted on a card after its handoff (self-heal agent-93112d1e96).

The handoff is the card's last run summary, frozen when that run ended. On 2026-11-11 David chose "Close scoped" for
the model-balancing incident; the decision was recorded as a comment on the already archived card, with no new run.
The Nov 13 close-out prompt still described the card as needing "a direct user waiver", so neither the agent's tool
log nor the coach judge saw the recorded decision, and the coach failed a correct reply. The board read now carries
up to MAX_LATER_COMMENTS comments created after the card's last run ended (or after completed_at without a run), and
the prompt prints them under the handoff on a "later on this card (after its handoff)" line.
"""
import importlib.util
import logging
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_plugin_later_comments', ROOT / '__init__.py')
plugin = importlib.util.module_from_spec(spec)
plugin.__package__ = ''
spec.loader.exec_module(plugin)
core = plugin.core

HANDOFF = ("Closed without delivering: the fallback chain never ran live. Delivery needs authorization to narrow "
           "the completion or a direct user waiver.")
DECISION = "David explicitly selected Close scoped: accept the narrowed scope; no new live check unless he asks."
OLD = "Worker note before the run ended: starting fallback chain replay."


def _board(tmp_path, comments=(), runs=((100, 200, HANDOFF),), completed_at: 'int | None' = 200, status='archived'):
    db = tmp_path / 'kanban.db'
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE tasks (id TEXT, status TEXT, title TEXT, block_kind TEXT, completed_at INTEGER, "
              "result TEXT, human_request TEXT)")
    c.execute("CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, status TEXT, "
              "started_at INTEGER, ended_at INTEGER, summary TEXT)")
    c.execute("CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, "
              "author TEXT NOT NULL, body TEXT NOT NULL, created_at INTEGER NOT NULL)")
    c.execute("INSERT INTO tasks VALUES ('t_7e2ee45c', ?, 'Model balancing incident', NULL, ?, ?, NULL)",
              (status, completed_at, None if runs else HANDOFF))
    for started, ended, summary in runs:
        c.execute("INSERT INTO task_runs (task_id, status, started_at, ended_at, summary) VALUES (?,?,?,?,?)",
                  ('t_7e2ee45c', 'done', started, ended, summary))
    for author, body, at in comments:
        c.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?,?,?,?)",
                  ('t_7e2ee45c', author, body, at))
    c.commit()
    c.close()
    return db


def _prompt(db):
    cards = core.read_cards([db], ['t_7e2ee45c'])
    return core.closeout_prompt({'batch': ['t_7e2ee45c']}, cards, now=1_000_000)


def _later_lines(prompt):
    return [ln for ln in prompt.splitlines() if 'later on this card (after its handoff)' in ln]


def test_later_decision_comment_is_shown_under_the_handoff_and_older_ones_are_not(tmp_path):
    db = _board(tmp_path, comments=[('default', OLD, 150), ('default', DECISION, 1_762_900_000)])
    prompt = _prompt(db)
    lines = _later_lines(prompt)
    assert len(lines) == 1, prompt
    assert DECISION in lines[0] and 'default' in lines[0]
    assert '2025-11-11' in lines[0] and 'UTC' in lines[0]  # the comment's own time, in UTC
    assert OLD not in prompt  # posted before the handoff: the handoff already covers it
    # printed directly under this card's handoff line
    body = prompt.splitlines()
    assert body.index(lines[0]) == next(i for i, ln in enumerate(body) if ln.lstrip().startswith('handoff:')) + 1


def test_no_run_anchors_on_completed_at(tmp_path):
    db = _board(tmp_path, runs=(), completed_at=500,
                comments=[('default', OLD, 400), ('default', DECISION, 600)])
    lines = _later_lines(_prompt(db))
    assert len(lines) == 1 and DECISION in lines[0] and OLD not in lines[0]


def test_bounds_hold(tmp_path):
    chatter = [('worker', f'chatter {i} ' + 'x' * 900, 300 + i) for i in range(10)]
    db = _board(tmp_path, comments=chatter + [('default', DECISION, 250)])
    lines = _later_lines(_prompt(db))
    assert 0 < len(lines) <= core.MAX_LATER_COMMENTS
    for ln in lines:
        text = ln.split(': ', 1)[1]
        assert len(text) <= core.MAX_LATER_COMMENT_CHARS + 80  # timestamp + author prefix
    # decision-like comments win over newer chatter
    assert any(DECISION in ln for ln in lines)


def test_prompt_total_is_capped_across_cards(tmp_path):
    cards = {f't_{i:08x}': {'status': 'done', 'title': 'x', 'summary': 'h',
                            'later_comments': [{'at': 1_762_900_000, 'author': 'a', 'body': 'y' * 1000}] * 2}
             for i in range(40)}
    prompt = core.closeout_prompt({'batch': sorted(cards)}, cards, now=1_000_000)
    total = sum(len(ln) for ln in _later_lines(prompt))
    assert 0 < total <= core.MAX_LATER_COMMENTS_PROMPT


def test_board_without_comments_table_still_reads(tmp_path):
    db = tmp_path / 'kanban.db'
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE tasks (id TEXT, status TEXT, title TEXT, completed_at INTEGER, result TEXT)")
    c.execute("INSERT INTO tasks VALUES ('t_7e2ee45c', 'done', 'x', 1, 'h')")
    c.commit()
    c.close()
    assert _later_lines(_prompt(db)) == []


def test_detection_line_logged_per_prompt(tmp_path, caplog):
    db = _board(tmp_path, comments=[('default', DECISION, 1_762_900_000)])
    with caplog.at_level(logging.INFO, logger='chat_closeout'):
        _prompt(db)
    assert any('closeout_later_comments n=1' in r.getMessage() for r in caplog.records)


# t_01a1d6cdfa497d94: pins for mutants of 8e23ae92 that survived every chat-closeout test.
GUIDANCE = "supersedes what the handoff left open"


def test_blocked_card_anchors_on_its_run_end_not_completed_at(tmp_path):
    # Blocked: completed_at is NULL; the handoff is the run summary that ended at 200. Anchoring on completed_at
    # alone gives no anchor, and the decision would vanish.
    db = _board(tmp_path, status='blocked', completed_at=None,
                comments=[('default', OLD, 150), ('default', DECISION, 300)])
    lines = _later_lines(_prompt(db))
    assert len(lines) == 1 and DECISION in lines[0], lines


def test_decision_is_ranked_in_before_the_cut(tmp_path):
    # The decision is neither among the 2 oldest nor the newest: rank, cut to MAX_LATER_COMMENTS, then order.
    db = _board(tmp_path, comments=[('worker', 'chatter 300', 300), ('worker', 'chatter 310', 310),
                                    ('default', DECISION, 320), ('worker', 'chatter 330', 330)])
    lines = _later_lines(_prompt(db))
    assert len(lines) == 2, lines
    assert DECISION in lines[0] and 'chatter 330' in lines[1], lines


def test_guidance_line_prints_iff_a_later_line_prints(tmp_path):
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    with_later = _prompt(_board(tmp_path / 'a', comments=[('default', DECISION, 300)]))
    without = _prompt(_board(tmp_path / 'b', comments=[('default', OLD, 150)]))
    assert _later_lines(with_later) and GUIDANCE in with_later
    assert not _later_lines(without) and GUIDANCE not in without


def test_blank_later_comment_neither_prints_nor_takes_a_slot(tmp_path):
    db = _board(tmp_path, comments=[('worker', 'real 300', 300), ('worker', 'real 310', 310),
                                    ('default', '  \n\t ', 320)])
    lines = _later_lines(_prompt(db))
    assert len(lines) == 2, lines
    assert 'real 300' in lines[0] and 'real 310' in lines[1], lines
    assert core.rank_later_comments([{'at': 1, 'author': 'a', 'body': ' \n '}]) == []

"""A ::user-step followed by a fenced code block: the block is the step's literal payload (David pasted the
paraphrased title into Slack instead of the `!project request` command)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = load('fence_core', ROOT / 'core.py')
OWNER = {'gatewayId': 'test', 'profile': 'default', 'storedSessionId': 'session'}
HEAD = '**1. Open the Ask Eve channel and send this as a new message:**'
CMD = '!project request build the thing\nwith details'
REPLY = f'Ready.\n\n{HEAD}\n\n```text\n{CMD}\n```\n\nThen tell me when it is sent.'
RAW = [{'text': 'Send the !project request command in the Ask Eve Slack channel from your own account',
        'kind': 'do', 'verbatim_span': 'send this as a new message'}]


def migrated(tmp_path, reply=REPLY, raw=RAW):
    (step,) = core.validate(raw, reply, [])
    block = core.locate(reply, step['verbatim_span'])
    step = {**step, 'id': 'us_t', 'session': 'session', 'paragraph': block['text'] + (
        '\n\n' + step['payload'] if step.get('payload') else '')}
    return core.ActionStore(tmp_path / 'a.sqlite3').migrate_step(OWNER, step), step


def test_scope_markdown_contains_the_fenced_literal(tmp_path):
    r, step = migrated(tmp_path)
    assert step['payload'].startswith('```text') and CMD in step['payload']
    assert HEAD in r['scope']['markdown'] and '```text\n' + CMD + '\n```' in r['scope']['markdown']


def test_title_neither_paraphrases_nor_drops_a_multiline_payload(tmp_path):
    r, _ = migrated(tmp_path)
    assert 'Ask Eve Slack channel from your own account' not in r['title']
    assert r['title'] == 'Use the exact text in the block below'


def test_title_quotes_a_short_single_line_literal(tmp_path):
    reply = f'{HEAD}\n\n```\n!project request x\n```'
    r, _ = migrated(tmp_path, reply)
    assert r['title'] == 'Use this exact text: !project request x'


def test_title_that_already_quotes_the_literal_is_kept():
    assert core.payload_title('Send !project request x', '```\n!project request x\n```') == 'Send !project request x'


def test_no_fence_no_change(tmp_path):
    reply = f'{HEAD}\n\nThen tell me.'
    (step,) = core.validate(RAW, reply, [])
    assert 'payload' not in step and step['text'] == RAW[0]['text']


def test_fence_after_other_prose_is_not_the_payload():
    reply = f'{HEAD}\n\nSome other paragraph.\n\n```\nls\n```'
    assert core.following_fence(reply, core.locate(reply, 'send this as a new message')) == ''

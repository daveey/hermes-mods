"""Quoted ask phrases, one item per block, and the question as the title (four junk For-you items, 2026-10-29).

Fixtures mirror the structure of the two real replies with neutral wording; the real texts stay private.
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('quoted_asks_core', ROOT / 'core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

QUESTION = 'Should they be blocked from that one database file and that one tool?'

DECISION_LAST = (
    "Both checks pass. One decision needs you; it's at the bottom.\n\n"
    "**Your decision:** the build agents can write to the hub database and run its grant tool. " + QUESTION + "\n"
    "- **Yes:** an agent adds the block and proves it holds.\n"
    "- **No:** it is recorded as an accepted risk.\n\n"
    "Reply yes or no here."
)

STATUS_AND_ASK = (
    'The earlier fix passes.\n\n'
    '**Also**\n'
    '- I withdrew an item from your "For you" list titled only "Reply yes or no here.", which had no question in it.\n'
    '- **PR 12:** CI is still running (3 passed, 3 pending).\n\n'
    "**Still needs you:** " + QUESTION.replace('Should', 'should') + " The ask reads:\n"
    "> Decide whether the build profiles get a deny on the database and the grant CLI\n\n"
    "I'd say yes: without the block, they could grant themselves access. Reply yes or no here."
)


def steps(*items):
    return [{'text': t, 'kind': 'approve', 'verbatim_span': sp, 'options': []} for t, sp in items]


def run(reply, raw):
    return core.validate(raw, reply, [])


def test_ask_item_takes_the_question_above_as_its_title_model_path():
    out = run(DECISION_LAST, steps(('Reply yes or no here.', 'Reply yes or no here.')))
    assert [s['text'] for s in out] == [QUESTION]


def test_ask_item_takes_the_question_above_as_its_title_regex_path():
    out = core.extract(DECISION_LAST, [], '', None)['steps']
    assert [s['text'] for s in out] == [QUESTION]


def test_status_list_that_only_quotes_the_phrase_yields_nothing():
    quoted = 'I withdrew an item from your "For you" list titled only "Reply yes or no here.", which had no question in it.'
    for span in ('Reply yes or no here.', quoted, 'titled only "Reply yes or no here."'):
        reply = STATUS_AND_ASK.split('**Still needs you:**')[0]
        assert run(reply, steps(('Reply yes or no here.', span))) == [], span
        assert core.extract(reply, [], '', None)['steps'] == [], span


def test_one_block_yields_at_most_one_item():
    reply = 'Status.\n\nCan you approve the deploy? Can you also pick a region for it?'
    out = run(reply, steps(('Approve the deploy', 'Can you approve the deploy?'),
                           ('Pick a region', 'Can you also pick a region for it?')))
    assert len(out) == 1


def test_recommendation_item_is_titled_with_the_question_across_a_quote_block():
    out = run(STATUS_AND_ASK, steps(("I'd say yes: without the block, they could grant themselves access.",
                                     "I'd say yes: without the block, they could grant themselves access. Reply yes or no here.")))
    assert [s['text'] for s in out] == [QUESTION]


def test_real_reply_yields_exactly_one_item_each_through_the_extractor():
    # the model's junk steps for the second reply: the quoted phrase twice and the recommendation
    junk = steps(('Reply yes or no here.', 'Reply yes or no here.'),
                 ('Reply yes or no here.', 'titled only "Reply yes or no here."'),
                 ("I'd say yes: without the block", "I'd say yes: without the block, they could grant themselves access. Reply yes or no here."))
    out = core.extract(STATUS_AND_ASK, [], '', lambda *a: json.dumps({'steps': junk, 'resolved': []}))['steps']
    assert [s['text'] for s in out] == [QUESTION]


def test_plain_real_ask_is_still_extracted_with_its_own_text():
    reply = 'The build is ready.\n\nShould I deploy build 412 to production?'
    out = run(reply, steps(('Approve deploying build 412 to production', 'Should I deploy build 412 to production?')))
    assert [s['text'] for s in out] == ['Approve deploying build 412 to production']
    assert [s['text'] for s in core.extract(reply, [], '', None)['steps']] == ['Should I deploy build 412 to production?']


def test_bare_phrase_span_resolves_to_the_block_that_says_it_not_the_one_that_quotes_it():
    out = run(STATUS_AND_ASK, steps(('Reply yes or no here.', 'Reply yes or no here.')))
    assert [s['text'] for s in out] == [QUESTION]

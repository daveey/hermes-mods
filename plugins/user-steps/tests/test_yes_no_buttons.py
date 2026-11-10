"""A single-proposition ask ('Decide whether to X', 'Should I X?') gets Yes/No buttons.

Live incident (hermes-hub 2026-11-10, session 20261110_182947_f0f6a3): the ask "Decide whether to grow the hub
disk further to 800 GB" had model options that were paraphrases ('Keep it at 600 GB', 'Grow to 800 GB (+$16/month)')
not found verbatim in the reply, the span had no '?' and the title had no 'or', so every literal-option source came
back empty and the card showed only 'Answer in your own words'.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('yes_no_core', ROOT / 'core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

LIVE = {
    'text': 'Decide whether to grow the hub disk further to 800 GB (about +$16/month)',
    'kind': 'choose',
    'verbatim_span': "If you meant go further, to 800 GB, say so; that's about another +$16/month.",
    'paragraph': "If you meant go further, to 800 GB, say so; that's about another +$16/month.",
    'options': ['Keep it at 600 GB', 'Grow to 800 GB (+$16/month)'],
}
OWNER = {'gatewayId': 'g', 'profile': 'default', 'storedSessionId': 's'}


def labels(step):
    return [c['label'] for c in core.legacy_request(OWNER, step, {'kind': 'step', 'id': 'x'})['choices']]


def test_live_whether_ask_gets_yes_no():
    assert labels(dict(LIVE)) == ['Yes', 'No', 'Answer in your own words']


def test_yes_no_question_span_gets_yes_no():
    step = {'text': 'Approve restarting the gateway', 'kind': 'input',
            'verbatim_span': 'Should I restart the gateway now?', 'paragraph': 'Should I restart the gateway now?',
            'options': []}
    assert core.literal_options(step) == ['Yes', 'No']


def test_either_or_ask_gets_no_yes_no():
    step = {'text': 'Decide whether to turn the kitchen speaker microphone back on or keep it off', 'kind': 'input',
            'verbatim_span': 'Should the agent turn it back on, or keep it off?',
            'paragraph': 'Should the agent turn it back on, or keep it off?', 'options': []}
    assert 'Yes' not in core.literal_options(step)


def test_open_question_gets_no_yes_no():
    step = {'text': 'Pick the model ID to use', 'kind': 'input',
            'verbatim_span': 'Which model ID should I use?', 'paragraph': 'Which model ID should I use?', 'options': []}
    assert core.literal_options(step) == []


def test_verbatim_options_still_win():
    step = {'text': 'Choose the hub disk size', 'kind': 'choose',
            'verbatim_span': 'Choices: **Grow to 600 GB** / **Keep 400 GB**.',
            'paragraph': 'Choices: **Grow to 600 GB** / **Keep 400 GB**.', 'options': ['Grow to 600 GB', 'Keep 400 GB']}
    assert core.literal_options(step) == ['Grow to 600 GB', 'Keep 400 GB']

"""A choose ask whose options are a lettered list ('- **(a) Head.** detail') gets one button per item.

Live incident (hermes-hub 2026-11-12, session 20261111_055255_6e01c3): the D1/D2 asks were written as bold
lettered bullets, the model's options were paraphrases ('D1 c (accept with waiver, recommended)') not found
verbatim, the span had no '?', so every literal source came back empty and the ask showed only
'Answer in your own words'.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('lettered_core', ROOT / 'core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

D1 = {
    'text': "Pick D1: (a) raise the price of killing, (b) make 'maximize rabbits' count foxes, or (c) accept the "
            "exploit for 1a with a written waiver",
    'kind': 'choose',
    'verbatim_span': '- **(a) Raise the price of killing.** This is a weak fix:',
    'options': ['D1 c (accept with waiver, recommended)', 'D1 b (count foxes in scoring)',
                'D1 a (raise price of killing)'],
    'paragraph': '**D1. Holding foxes at 3 beats the heuristic god at "maximize rabbits."** The careful culler wins.\n'
                 '- **(a) Raise the price of killing.** This is a weak fix:\n'
                 '  - the culler leaves more than half its energy unspent, so a small rise does nothing;\n'
                 '  - finding the right price would take several reruns on worker-2.\n'
                 '- **(b) Make "maximize rabbits" count foxes too.** The data says this works.\n'
                 '- **(c) Accept the exploit for 1a, with a written waiver.** The checks run again in Phase 8.',
}
D2 = {
    'text': 'Pick D2: (a) record the rain overspend as a quirk of that god, (b) give that god a third tool, or (c) '
            'loosen the check',
    'kind': 'choose',
    'verbatim_span': "- **(a) Record it as a quirk of that god** and check it again from the model's play.",
    'options': ['D2 a (record as quirk, recommended)', 'D2 b (give god a third tool)', 'D2 c (loosen the check)'],
    'paragraph': '**D2. Rain is 81-82% of the spending, against an 80% limit.** Only 11-36 energy over.\n'
                 "- **(a) Record it as a quirk of that god** and check it again from the model's play.\n"
                 '- **(b) Give that god a third tool.** This changes a reviewed baseline god.\n'
                 '- **(c) Loosen the check.** This weakens a gate after seeing the result.',
}
OWNER = {'gatewayId': 'g', 'profile': 'default', 'storedSessionId': 's'}


def choices(step):
    return core.legacy_request(OWNER, step, {'type': 'user_step', 'id': 'x'})['choices']


def test_live_d1_lettered_items_become_buttons_with_recommendation():
    cs = choices(dict(D1))
    assert [c['label'] for c in cs] == ['(a) Raise the price of killing', '(b) Make "maximize rabbits" count foxes too',
                                        '(c) Accept the exploit for 1a, with a written waiver',
                                        'Answer in your own words']
    assert [c['label'] for c in cs if c.get('recommended')] == ['(c) Accept the exploit for 1a, with a written waiver']


def test_live_d2_bold_head_without_period():
    cs = choices(dict(D2))
    assert [c['label'] for c in cs][:3] == ['(a) Record it as a quirk of that god', '(b) Give that god a third tool',
                                            '(c) Loosen the check']
    assert [c['label'] for c in cs if c.get('recommended')] == ['(a) Record it as a quirk of that god']


def test_lettered_labels_are_literal_slices_of_the_source():
    src = core._unmarked(D1['paragraph']).casefold()
    for label in core.literal_options(dict(D1)):
        assert label.casefold() in src


def test_single_lettered_item_is_not_a_menu():
    step = dict(D1, paragraph='Notes:\n- (a) Only one item here.\nNothing else.', options=[])
    assert core.literal_options(step) == []


def test_non_consecutive_codes_are_not_a_menu():
    step = dict(D1, paragraph='- (a) First thing here.\n- (c) Third thing here.', options=[])
    assert core.literal_options(step) == []


def test_verbatim_model_options_still_win():
    step = dict(D1, options=['Raise the price of killing', 'Accept the exploit for 1a'])
    assert core.literal_options(step) == ['Raise the price of killing', 'Accept the exploit for 1a']


def test_no_recommendation_without_a_marked_option():
    step = dict(D1, options=['D1 c (accept with waiver)', 'D1 b (count foxes)'])
    assert not any(c.get('recommended') for c in choices(step))

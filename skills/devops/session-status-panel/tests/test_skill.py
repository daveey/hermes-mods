"""Keep the panel workflow's references and brief example executable offline."""
import importlib.util
from pathlib import Path
import re

SKILL = Path(__file__).resolve().parents[1]
ROOT = SKILL.parents[2]


def test_frontmatter_and_contract_links():
    text = (SKILL / 'SKILL.md').read_text()
    front = text.split('---', 2)[1]
    description = re.search(r'^description: "(.+)"$', front, re.M).group(1)
    assert len(description) <= 60 and description.endswith('.')
    for name in ('name', 'version', 'author', 'license', 'platforms', 'metadata', 'triggers'):
        assert re.search(rf'^{name}:', front, re.M)
    for target in re.findall(r'\]\(([^)]+)\)', text):
        assert (SKILL / target).resolve().is_file(), target
    assert (ROOT / 'skills/devops/generate-unblock-card/SKILL.md').is_file()


def test_documented_choice_names_and_letter_join_use_real_parser():
    spec = importlib.util.spec_from_file_location('skill_brief', ROOT / 'plugins/session-panel/brief.py')
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    request = {
        'scope': 'Situation: A proof needs approval.\nOptions:\nA) Fund proof: money and time; reversible.\nC) Test live: bounded test; reversible.\nRecommend: C - test before spending.\n\nEvidence footer is not a reason.',
        'why_human': 'Tried: checked both routes. Only you can: approve the spend.',
        'choices': [{'label': 'C: Test live (recommended)', 'value': 'opaque-1'},
                    {'label': 'A: Fund proof', 'value': 'opaque-2'}],
    }
    result = parser.parse_brief(request)
    assert [(o['letter'], o['name'], o['detail']) for o in result['options']] == [
        ('C', 'Test live', 'Bounded test; reversible.'),
        ('A', 'Fund proof', 'Money and time; reversible.'),
    ]
    assert result['recommend_letter'] == 'C'
    assert result['reason'] == 'test before spending.'
    assert result['only_you'] == 'Only you can: approve the spend.'
    assert result['tried'] == 'checked both routes.'


def test_unblock_skill_cross_reference_and_no_old_layout_contract():
    text = (ROOT / 'skills/devops/generate-unblock-card/SKILL.md').read_text()
    assert '[session-status-panel](../session-status-panel/SKILL.md)' in text
    assert 'choices' in text and 'their labels supply tile order and names' in text
    assert 'clamped to two' not in text
    assert 'filled **Choose** button' not in text

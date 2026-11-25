"""Acceptance cannot report green on skipped/missing/failed composed cases."""
import xml.etree.ElementTree as ET
import pytest
from test_withdrawal import ROOT, load_module

results = load_module('composed_results_tests', ROOT / 'composed_results.py')


def reports(directory):
    for stem, names in results.REQUIRED.items():
        root = ET.Element('testsuites')
        suite = ET.SubElement(root, 'testsuite')
        for name in sorted(names):
            ET.SubElement(suite, 'testcase', name=name)
        ET.ElementTree(root).write(directory / (stem + '.xml'))


RELEASE_CASES = {
    'test_default_unblock_on_a_scope_stopped_card_releases_it',
    'test_default_unblock_on_a_decision_card_releases_its_root',
    'test_custom_choice_ask_is_unchanged',
    'test_core_without_release_keeps_todays_unblock',
    'test_safety_refusal_leaves_the_ask_and_records_the_reason',
    'test_default_unblock_on_a_card_no_stop_holds_is_todays_unblock',
    'test_retry_after_a_crash_between_release_and_receipt_records_the_receipt[False]',
    'test_retry_after_a_crash_between_release_and_receipt_records_the_receipt[True]',
    'test_an_ask_that_did_not_offer_a_release_keeps_the_plain_unblock[agent]',
    'test_an_ask_that_did_not_offer_a_release_keeps_the_plain_unblock[old_text]',
    'test_a_stamp_an_agent_block_supplies_is_dropped_by_core',
    'test_a_stale_decision_card_does_not_release_a_later_stop',
    'test_a_stamped_ask_this_board_did_not_file_keeps_the_plain_unblock',
}


def test_the_unblock_click_release_cases_are_required():
    # The Desktop Unblock click through real core's `hermes kanban release` (test_kanban_mirror_release_core.py)
    # skips on a core without kanban_release; CI must count each case, never a skip.
    assert results.REQUIRED['test_kanban_mirror_release_core'] == RELEASE_CASES
    assert 'test_kanban_mirror_release_core' in (ROOT / 'run-core-integration.sh').read_text()


def test_complete_manifest_passes(tmp_path, capsys):
    reports(tmp_path)
    assert results.verify(tmp_path) == 22
    assert '22 required PASS; 22 total PASS; 0 SKIP' in capsys.readouterr().out


@pytest.mark.parametrize('mutation', ['skipped', 'failure', 'error', 'missing-case',
                                      'missing-report', 'duplicate', 'malformed'])
def test_nonpassing_manifest_fails_closed(tmp_path, mutation):
    reports(tmp_path)
    path = tmp_path / 'test_remote_card_async_custody.xml'
    tree = ET.parse(path)
    suite = tree.getroot().find('testsuite')
    case = suite.find('testcase')
    if mutation in ('skipped', 'failure', 'error'):
        ET.SubElement(case, mutation)
    elif mutation == 'missing-case':
        suite.remove(case)
    elif mutation == 'duplicate':
        ET.SubElement(suite, 'testcase', name=case.attrib['name'])
    tree.write(path)
    if mutation == 'missing-report':
        path.unlink()
    elif mutation == 'malformed':
        path.write_text('not XML')
    with pytest.raises((ValueError, OSError, ET.ParseError)):
        results.verify(tmp_path)

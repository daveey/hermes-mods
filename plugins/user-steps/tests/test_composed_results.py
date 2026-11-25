"""Acceptance cannot report green on skipped/missing/failed composed cases."""
import re
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
    'test_default_unblock_on_a_plain_stop_asked_on_the_held_card_releases_it',
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


LOCAL_AUTHORITY_CASES = {
    'test_local_authority_core': {
        'test_real_pending_click_grants_and_acknowledges',
        'test_stamped_offer_old_core_refuses_not_plain_unblock',
        'test_old_plugin_plain_unblock_is_refused_by_new_core',
        'test_stamped_offer_wrong_adapter_version_refuses',
    },
    'test_local_authority_core_refusals': {
        'test_reviewer_archive_stamped_offer',
        'test_reviewer_real_adapter_refusal',
        'test_reviewer_remote_precedes_local_adapter',
    },
    'test_local_authority_core_binding': {
        'test_ask_without_an_offer_on_this_card_keeps_the_plain_unblock[stamp-names-another-card]',
        'test_ask_without_an_offer_on_this_card_keeps_the_plain_unblock[unstamped]',
        'test_target_card_with_a_stamp_naming_another_card_is_refused_by_core',
        'test_malformed_stamp_on_the_target_stays_on_the_adapter[text]',
        'test_malformed_stamp_on_the_target_stays_on_the_adapter[null]',
        'test_malformed_stamp_on_the_target_stays_on_the_adapter[task-not-text]',
        'test_malformed_stamp_on_the_target_stays_on_the_adapter[empty]',
        'test_stamped_other_text_is_refused_by_the_adapter_and_writes_nothing',
    },
}


def test_the_local_authority_cases_are_required_and_run():
    # The stamped local answer through real core's adapter skips on a core without kanban_selfwork_local, and
    # everywhere outside run-core-integration.sh; CI must run and count each case, never a skip.
    runner = (ROOT / 'run-core-integration.sh').read_text()
    composed = runner.split('--remote-card-composed', 1)[1].split('composed_results.py', 1)[0]
    for stem, cases in LOCAL_AUTHORITY_CASES.items():
        assert results.REQUIRED[stem] == cases
        assert re.search(rf'\b{stem}\b', composed)
        defined = set(re.findall(r'(?m)^def (test_\w+)', (ROOT / 'tests' / (stem + '.py')).read_text()))
        assert defined == {name.split('[')[0] for name in cases}


def test_complete_manifest_passes(tmp_path, capsys):
    reports(tmp_path)
    assert results.verify(tmp_path) == 38
    assert '38 required PASS; 38 total PASS; 0 SKIP' in capsys.readouterr().out


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

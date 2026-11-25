"""Fail-closed acceptance for real guarded remote-card composed tests."""
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

REQUIRED = {
    'test_remote_card_async_custody': {
        'test_async_outbox_absence_observation_race[no-race]',
        'test_async_outbox_absence_observation_race[race]',
    },
    'test_remote_card_review_race': {
        f'test_successful_owner_delivery_not_retired_by_replica_refresh[{mode}]'
        for mode in ('none', 'departure', 'reblock')
    },
    'test_remote_card_courier': {
        f'test_mirrored_real_store_answer_receipt_against_owner[{other}-{stale}]'
        for other in ('False', 'True') for stale in ('False', 'True')
    },
    # The Desktop Unblock click releasing a stopped card through real core's `hermes kanban release`.
    # It skips on a core without hermes_cli/kanban_release.py, so every case is named here.
    'test_kanban_mirror_release_core': {
        'test_default_unblock_on_a_scope_stopped_card_releases_it',
        'test_default_unblock_on_a_decision_card_releases_its_root',
        'test_custom_choice_ask_is_unchanged',
        'test_core_without_release_keeps_todays_unblock',
        'test_safety_refusal_leaves_the_ask_and_records_the_reason',
        'test_default_unblock_on_a_card_no_stop_holds_is_todays_unblock',
        *(f'test_retry_after_a_crash_between_release_and_receipt_records_the_receipt[{on}]'
          for on in ('False', 'True')),
        *(f'test_an_ask_that_did_not_offer_a_release_keeps_the_plain_unblock[{author}]'
          for author in ('agent', 'old_text')),
        'test_a_stamp_an_agent_block_supplies_is_dropped_by_core',
        'test_a_stale_decision_card_does_not_release_a_later_stop',
        'test_a_stamped_ask_this_board_did_not_file_keeps_the_plain_unblock',
    },
}
REQUIRED_COUNT = sum(len(names) for names in REQUIRED.values())


def verify(directory):
    total = 0
    for stem, required in REQUIRED.items():
        # Missing, malformed or overwritten reports must not become a green run.
        root = ET.parse(Path(directory) / (stem + '.xml')).getroot()
        cases = root.findall('.//testcase')
        names = [case.attrib['name'] for case in cases]
        if len(set(names)) != len(names):
            raise ValueError(f'{stem}: duplicate case results')
        if not required <= set(names):
            raise ValueError(f'{stem}: missing required cases {sorted(required - set(names))}')
        for case in cases:
            if any(case.find(tag) is not None for tag in ('skipped', 'failure', 'error')):
                raise ValueError(f'{stem}: non-passing case {case.attrib["name"]}')
        for name in sorted(required):
            print(f'PASS {stem}.py::{name}')
        total += len(cases)
    print(f'Guarded remote-card composed: {REQUIRED_COUNT} required PASS; {total} total PASS; 0 SKIP')
    return total


if __name__ == '__main__':
    verify(sys.argv[1])

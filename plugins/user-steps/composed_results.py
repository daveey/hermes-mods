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
}


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
    print(f'Guarded remote-card composed: 9 required PASS; {total} total PASS; 0 SKIP')
    return total


if __name__ == '__main__':
    verify(sys.argv[1])

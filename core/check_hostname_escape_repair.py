"""Whole-tree check for the additive 2253 escape repair (not a replay substitute)."""
import argparse
import json
from pathlib import Path

import repair_hostname_escapes as repair
import verify_mirror as mirror

HERE = Path(__file__).resolve().parent


def checked_policy():
    original = json.loads((HERE / 'mirror-policy-2253.json').read_text())
    repaired = json.loads((HERE / 'mirror-policy-2253-escape-repair.json').read_text())
    if original['fingerprints'][repair.TARGET][1] != repair.BEFORE:
        raise ValueError('Historical preimage binding changed')
    original['fingerprints'][repair.TARGET][1] = repair.AFTER
    if repaired != original:
        raise ValueError('Repair policy must change only the exact overlay postimage')
    return repaired


def check(repo, applied):
    policy = checked_policy()
    rules, fingerprints, renames = mirror.load_policy(
        HERE / 'mirror-policy-2253-escape-repair.json', policy['source_tip'], policy['base'])
    mirror.compare(repo, policy['source_tip'], applied, rules, fingerprints, renames)
    data = mirror.git(repo, 'show', applied + ':' + repair.TARGET)
    if repair.blob_id(data) != repair.AFTER:
        raise ValueError('Overlay postimage not present')
    repair.validate_markers(data)
    return {'source_tip': policy['source_tip'], 'overlay_blob': repair.AFTER,
            'whole_tree': 'pass', 'marker_compile': 'pass'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-repo', required=True)
    parser.add_argument('--applied', required=True, help='Committed repaired tree in source object store')
    args = parser.parse_args()
    try:
        result = check(args.source_repo, args.applied)
    except (mirror.VerificationError, ValueError, KeyError, OSError):
        parser.error('Repaired tree verification failed')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()

# Snapshot-bound additive regex repair and whole-tree check for mirror 4142.
import argparse
import json
from pathlib import Path
import repair_hostname_escapes as repair
import verify_mirror as mirror
HERE = Path(__file__).resolve().parent
BEFORE = '5965a73dce84565aacea66d2091d05baeead4833'
AFTER = '8f561a6010a50916acb0fc96c6016084a5ecd988'

def checked_policy():
    original = json.loads((HERE / 'mirror-policy-4142.json').read_text())
    repaired = json.loads((HERE / 'mirror-policy-4142-escape-repair.json').read_text())
    if original['fingerprints'][repair.TARGET][1] != BEFORE:
        raise ValueError('Historical preimage binding changed')
    original['fingerprints'][repair.TARGET][1] = AFTER
    if original != repaired:
        raise ValueError('Repair policy must change only the overlay postimage')
    return repaired

def repair_bytes(data):
    if repair.blob_id(data) != BEFORE:
        raise ValueError('Overlay requires the exact snapshot preimage')
    old, new = br'\mini-1\b', br'\bmini-1\b'
    if data.count(old) != 3:
        raise ValueError('Unexpected marker count')
    result = data.replace(old, new)
    repair.validate_markers(result)
    if repair.blob_id(result) != AFTER:
        raise ValueError('Overlay postimage mismatch')
    return result

def check(repo, applied):
    policy = checked_policy()
    rules, fingerprints, renames = mirror.load_policy(
        HERE / 'mirror-policy-4142-escape-repair.json', policy['source_tip'], policy['base'])
    mirror.compare(repo, policy['source_tip'], applied, rules, fingerprints, renames)
    data = mirror.git(repo, 'show', applied + ':' + repair.TARGET)
    if repair.blob_id(data) != AFTER:
        raise ValueError('Overlay postimage not present')
    repair.validate_markers(data)
    return dict(source_tip=policy['source_tip'], overlay_blob=AFTER,
                whole_tree='pass', marker_compile='pass')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tree', type=Path)
    parser.add_argument('--source-repo')
    parser.add_argument('--applied')
    args = parser.parse_args()
    try:
        if args.tree and not args.source_repo and not args.applied:
            checked_policy()
            path = args.tree / repair.TARGET
            if path.is_symlink():
                raise ValueError('Overlay target must not be a symlink')
            path.write_bytes(repair_bytes(path.read_bytes()))
            print(AFTER)
        elif args.source_repo and args.applied and not args.tree:
            print(json.dumps(check(args.source_repo, args.applied), sort_keys=True))
        else:
            parser.error('Choose --tree or --source-repo with --applied')
    except (mirror.VerificationError, ValueError, KeyError, OSError) as error:
        parser.error(str(error))

if __name__ == '__main__':
    main()

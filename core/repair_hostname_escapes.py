"""Escape-safe export and exact-preimage repair for the historical 2253 mirror.

Historical verify_mirror.normalize stays unchanged: old policies describe old
bytes. normalize_regex accepts REGEX VALUES, not source text or Windows paths.
Exporters must distinguish those contexts and validate affected regex tables.
"""
import argparse
import ast
import hashlib
from pathlib import Path
import re

from verify_mirror import normalize

BEFORE = '2be6b779c96825ea64a86120851eb484ec866e83'
AFTER = 'fa4cceb74abf284504a725d3e1ad5b7421fdc570'
TARGET = 'hermes_cli/kanban_placement.py'
# Preserve word-boundary tokens, including escaped serialization of those tokens.
BOUNDARY = re.compile(rb'(\\+[bB])')


def normalize_regex(data, substitutions):
    parts = BOUNDARY.split(data)
    return b''.join(part if index % 2 else normalize(part, substitutions)
                    for index, part in enumerate(parts))


def validate_markers(data):
    """Compile the literal default table without importing/executing source code."""
    tree = ast.parse(data)
    literals = {}
    table = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    literals[target.id] = node.value.value
                if target.id == 'DEFAULT_LOCAL_MARKERS':
                    table = node.value
    if not isinstance(table, (ast.Tuple, ast.List)) or not table.elts:
        raise ValueError('Missing literal marker table')
    for item in table.elts:
        if isinstance(item, ast.Name):
            value = literals[item.id]
        else:
            value = ast.literal_eval(item)
        if not isinstance(value, str):
            raise ValueError('Non-string marker')
        re.compile(value)


def blob_id(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def repair_bytes(data):
    if blob_id(data) != BEFORE:
        raise ValueError('Overlay requires the exact historical preimage')
    old, new = br'\mini-1\b', br'\bmini-1\b'
    if data.count(old) != 2:
        raise ValueError('Unexpected marker occurrence count')
    result = data.replace(old, new)
    validate_markers(result)
    if blob_id(result) != AFTER:
        raise ValueError('Overlay postimage mismatch')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tree', type=Path, help='Disposable replay checkout after entry 2253')
    args = parser.parse_args()
    path = args.tree / TARGET
    if path.is_symlink():
        parser.error('Overlay target must not be a symlink')
    try:
        repaired = repair_bytes(path.read_bytes())
    except (ValueError, KeyError, OSError, re.error) as error:
        parser.error(str(error))
    path.write_bytes(repaired)
    print(blob_id(repaired))


if __name__ == '__main__':
    main()

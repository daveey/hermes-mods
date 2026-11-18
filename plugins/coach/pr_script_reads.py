"""Conservative provenance for PR-state scripts observed in this turn.

This is not a shell interpreter or an attestation of remote GitHub identity. Only
verified tool writes of a small read-only shell grammar qualify. Results alone,
quoted transcripts, unknown commands, and files read from disk never qualify.
"""
import json
import os
import re
import shlex

CAP = 20000
PREFIX = '   <- PR state result: '
# Only tools whose contracts cannot edit the saved script preserve provenance.
# Unknown/new tools fail closed; broad executors and delegators are not readers.
READ_ONLY_TOOLS = frozenset({
    'read_file', 'search_files', 'skill_view', 'skills_list',
    'tool_describe', 'hermes_tool_search',
})
ROW = re.compile(r'(?:[\w-]+)?#([1-9]\d*) (OPEN|CLOSED|MERGED) merged=(null|[0-9TZ:.-]+) head=([0-9a-f]{7,40})')


def obj(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def path_key(path):
    # Match literal $HOME/~/absolute spellings, without opening any filesystem path.
    if not isinstance(path, str) or len(path) > 2000:
        return ''
    path = path.replace('${HOME}/', os.path.expanduser('~/')).replace('$HOME/', os.path.expanduser('~/'))
    path = os.path.expanduser(path)
    return os.path.normpath(path) if path.startswith('/') else ''


def script_ids(source):
    """Accept simple gh state projections, numeric loops and optional status reads.

    Unknown syntax rejects the entire script: no eval/source/functions/redirection,
    arbitrary echo, substitutions, or jq constants that could forge a state row.
    """
    if not isinstance(source, str) or len(source) > CAP:
        return set()
    ids, loop = {}, None
    for line in source.replace('\\\n', '').splitlines():
        line = line.strip()
        if line.startswith('#!') and line not in ('#!/usr/bin/env bash', '#!/bin/bash', '#!/bin/sh'):
            return set()
        if not line or line.startswith('#') or line in ('set -u', 'set -e', 'set -eu', 'set -euo pipefail'):
            continue
        if line == 'echo "read_at=$(date -u +%FT%TZ)"':
            continue
        m = re.fullmatch(r'for ([a-zA-Z_]\w*) in ([0-9 ]+); do', line)
        if m and loop is None:
            loop = (m[1], set(m[2].split()))
            if len(loop[1]) > 32:
                return set()
            continue
        if line == 'done' and loop:
            loop = None
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            return set()
        if parts[:3] == ['gh', 'pr', 'view'] and len(parts) == 10:
            _, _, _, number, repo_flag, repo, json_flag, fields, jq_flag, jq = parts
            if (repo_flag not in ('--repo', '-R') or not re.fullmatch(r'[\w.-]+/[\w.-]+', repo)
                    or json_flag != '--json' or set(fields.split(',')) != {'state', 'mergedAt', 'headRefOid'}
                    or jq_flag != '--jq'):
                return set()
            numbers = loop[1] if loop and number == '$' + loop[0] else {number}
            if not all(re.fullmatch(r'[1-9]\d*', n) for n in numbers):
                return set()
            # shlex joins adjacent quoted segments, including the loop's "$n".
            label = '$' + loop[0] if loop and number == '$' + loop[0] else number
            projection = r'"(?:[\w-]+)?#' + re.escape(label) + r' \\\(\.state\) merged=\\\(\.mergedAt\) head=\\\(\.headRefOid\[0:(?:12|40)\]\)"'
            if not re.fullmatch(projection, jq):
                return set()
            for n in numbers:
                ids.setdefault(n, set()).add(repo.lower())
            continue
        # Ancillary read-only status listing, whose output cannot grant PR evidence.
        if re.fullmatch(r"gh api repos/[\w.-]+/[\w.-]+/commits/[0-9a-f]{40}/statuses\s+--jq '\.\[\] \| \"#[0-9]+ status \\\(\.context\) \\\(\.state\) \\\(\.created_at\)\"' \| sort -u \| head -[1-9][0-9]?", line):
            continue
        return set()
    return ids if loop is None and len(ids) <= 32 else set()


def execution_path(command):
    """A direct script invocation, optionally chmod/broker/bash and a cut display pipe."""
    if not isinstance(command, str) or len(command) > CAP or '$(' in command or '`' in command:
        return ''
    try:
        lexer = shlex.shlex(command, posix=False, punctuation_chars=';&|<>')
        lexer.whitespace_split = True
        words = list(lexer)
    except ValueError:
        return ''
    if words[:2] == ['chmod', '+x'] and len(words) > 3 and words[3] == ';':
        words = words[4:]
    if words[:2] == ['token-broker', 'exec']:
        # No shell program in broker options or arbitrary option switches.
        if len(words) < 8 or words[2:4] != ['--scope', 'user/github'] or words[4] != '--reason' or words[6] != '--':
            return ''
        words = words[7:]
    if words and words[0] in ('bash', 'sh'):
        words = words[1:]
    if not words:
        return ''
    raw, rest = words[0], words[1:]
    if raw.startswith("'"):
        raw = raw[1:-1]  # single quotes never expand HOME or tilde
        path = raw if raw.startswith('/') else ''
    else:
        quoted = raw.startswith('"')
        raw = raw[1:-1] if quoted else raw
        if (quoted and raw.startswith('~')) or (not quoted and re.search(r'[{}]', raw)):
            # Bash expands unquoted brace paths before invoking the script.
            # shlex does not, so accepting them would credit a different file.
            return ''
        path = path_key(raw)
    if not path or re.search(r'[$`\\*?\[\]]', path):
        return ''
    if rest[:4] == ['2', '>&', '1', '|']:
        rest = rest[3:]
    if rest and (len(rest) != 3 or rest[:2] != ['|', 'cut'] or not re.fullmatch(r'-c1-[1-9][0-9]{0,3}', rest[2])):
        return ''
    return path


def observe(state, tool, args, result, ok):
    kept = state.setdefault('pr_read_scripts', {})
    data = obj(result)
    if tool in ('write_file', 'patch'):
        path = path_key(data.get('resolved_path') or args.get('path'))
        kept.clear()  # any mutation breaks the verified-write -> run chain
        if tool == 'write_file' and path and ok and data.get('verified') is True:
            ids = script_ids(args.get('content'))
            if ids and path_key(data.get('resolved_path', args.get('path'))) == path:
                if len(kept) >= 8:
                    kept.pop(next(iter(kept)))
                kept[path] = ids
        return []
    if tool != 'terminal':
        if tool not in READ_ONLY_TOOLS:
            kept.clear()
        return []
    path = execution_path(args.get('command'))
    ids = kept.get(path, {})
    # A non-recognized terminal command may mutate saved scripts. Drop their
    # provenance rather than trying to infer arbitrary shell writes.
    if not path or not ids:
        kept.clear()
        return []
    if not ids or not ok or type(data.get('exit_code')) is not int or data['exit_code'] != 0 or data.get('error'):
        return []
    output = data.get('output')
    if not isinstance(output, str) or len(output) > CAP:
        return []
    found = {}
    for line in output.splitlines():
        m = ROW.fullmatch(line)
        if m and m[1] in ids:
            found[m[1]] = line
    return [PREFIX + next(iter(ids[n])) + '#' + n for n in sorted(found, key=int) if len(ids[n]) == 1]

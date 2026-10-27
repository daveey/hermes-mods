"""Refuse brittle PR terminal-state wake checks before a Kanban park."""
from __future__ import annotations

import json
import re

TOOLS = {'kanban_block', 'kanban_schedule'}
# Allow global gh flags before the subcommand, command substitutions and pipelines.
GH_PR = re.compile(r'\bgh\s+(?:[^;|&\n]*?\s+)?pr\s+(?:view|status)\b')
GH_API = re.compile(r'\bgh\s+(?:[^;|&\n]*?\s+)?api\b[^;|&\n]*?/pulls/\d+\b')
STATUS = re.compile(r'\b(?:state|merged|mergedAt|merged_at|mergeStateStatus|mergeable)\b', re.I)
PROBE = 'hermes --run-module hermes_cli.kanban_pr_wake https://github.com/OWNER/REPO/pull/N'
REFUSAL = (
    'kanban-pr-wake-guard: refused raw gh PR state/merge wake_check. '
    'Graphite merge queues close the original PR (CLOSED, not MERGED), so this test can sleep forever. '
    f'Use {PROBE}. It wakes on merged OR closed; wake-up is not proof of delivery. '
    'For replacement-commit landings while the original stays OPEN, supply BOTH '
    '--superseding-commit <full 40-char sha> --verification-job <successful Actions job id on that sha>.'
)
WARNING = (
    'kanban-pr-wake-guard: this non-terminal gh PR probe may never wake if the PR closes '
    '(for example, reviewDecision never becomes APPROVED). '
    f'Use {PROBE} when waiting for a terminal PR state; reconcile closure on wake.'
)


def classify(probe):
    """Return block/warn/None; shared with read-only migration inventory."""
    if not isinstance(probe, str):
        return None
    probe = probe.replace('\\\n', '')
    # Comments are not execution; preserve # inside shell quotes.
    import shlex
    lexer = shlex.shlex(probe, posix=True)
    lexer.whitespace_split = True
    try:
        probe = ' '.join(lexer)
    except ValueError:
        pass  # malformed shell still gets the conservative textual check
    if re.search(r'\bhermes\s+--run-module\s+hermes_cli\.kanban_pr_wake\b', probe):
        return None
    if not (GH_PR.search(probe) or GH_API.search(probe)):
        return None
    # Repository/path names aren't requested fields or compared status values.
    fields = re.sub(r'(?:https?://)?[\w.{}-]+(?:/[\w.{}-]+)+', '', probe)
    return 'block' if STATUS.search(fields) else 'warn'


def checks(tool_name, args):
    if not isinstance(args, dict):
        return []
    if tool_name in TOOLS:
        return [classify(args.get('wake_check'))]
    if tool_name in {'tool_call', 'mcp__tool_call'}:
        calls = args.get('calls')
        if isinstance(calls, list):
            return [v for call in calls if isinstance(call, dict)
                    for v in checks(call.get('name'), call.get('arguments'))]
    return []


def pre_tool_call(tool_name='', args=None, **_):
    if 'block' in checks(tool_name, args):
        return {'action': 'block', 'message': REFUSAL}
    return None


def transform_tool_result(tool_name='', args=None, result=None, **_):
    if 'warn' not in checks(tool_name, args):
        return None
    if isinstance(result, str):
        if 'kanban-pr-wake-guard:' in result:
            return None
        try:
            data = json.loads(result)
        except (ValueError, TypeError):
            return result + '\n\nWarning: ' + WARNING
        if isinstance(data, dict):
            data['warning'] = (str(data['warning']) + '\n' if data.get('warning') else '') + WARNING
            return json.dumps(data)
        return result + '\n\nWarning: ' + WARNING
    if isinstance(result, dict):
        if 'kanban-pr-wake-guard:' in str(result.get('warning', '')):
            return None
        return {**result, 'warning': (str(result['warning']) + '\n' if result.get('warning') else '') + WARNING}
    return None


def register(ctx):
    ctx.register_hook('pre_tool_call', pre_tool_call)
    ctx.register_hook('transform_tool_result', transform_tool_result)

"""Exact system service identities; no glob expansion or shell."""
import re


def validate_service(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,230}\.service', value) or '..' in value:
        raise ValueError('invalid_service')
    return value


def service_command(value):
    return ['/usr/bin/systemctl', 'show', '--no-pager', '--property=LoadState,ActiveState', '--', validate_service(value)]


def service_state(output):
    fields = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    if fields.get('LoadState') == 'not-found':
        return 'missing'
    if fields.get('LoadState') != 'loaded':
        return 'unknown'
    return {'active': 'running', 'inactive': 'stopped', 'failed': 'failed'}.get(fields.get('ActiveState'), 'unknown')

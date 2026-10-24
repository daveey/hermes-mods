"""Fully qualified launchd domains only, never label search."""
import re


def validate_service(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:system|(?:gui|user)/[0-9]{1,10})/[A-Za-z0-9][A-Za-z0-9_.-]{0,230}', value) or '..' in value:
        raise ValueError('invalid_service')
    return value


def service_command(value):
    return ['/bin/launchctl', 'print', validate_service(value)]


def service_state(output):
    match = re.search(r'^\s*state = ([a-z ]+)\s*$', output, re.MULTILINE)
    return {'running': 'running', 'waiting': 'stopped', 'not running': 'stopped'}.get(match.group(1).strip() if match else '', 'unknown')

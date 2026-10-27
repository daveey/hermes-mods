"""Least-privilege boundary: credentials, probe key, broker install artifacts.

Fictional identifiers only. Nothing here reads real credentials or contacts a host.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from plugins.runners import credentials, operations as ops
from plugins.runners.broker import authorization
from plugins.runners.probes import ssh as probe_ssh

ROOT = Path(__file__).parents[1]
REF = 'cred_' + 'd' * 32
SECRET = 'ghs_' + 'Z' * 36


def _write(path, mode):
    path.write_text(SECRET + '\n')
    os.chmod(path, mode)
    return path


def test_credential_loader_accepts_only_private_owned_files(tmp_path):
    os.chmod(tmp_path, 0o700)
    _write(tmp_path / REF, 0o600)
    assert credentials.load_secret(tmp_path, REF, expected_uid=os.getuid()) == SECRET


@pytest.mark.parametrize('mode', [0o640, 0o644, 0o604, 0o660, 0o666])
def test_credential_loader_refuses_group_or_world_readable(tmp_path, mode):
    os.chmod(tmp_path, 0o700)
    _write(tmp_path / REF, mode)
    with pytest.raises(credentials.CredentialError) as exc:
        credentials.load_secret(tmp_path, REF, expected_uid=os.getuid())
    assert SECRET not in str(exc.value) and SECRET not in repr(exc.value)


def test_credential_loader_refuses_wrong_owner_symlink_dir_and_size(tmp_path):
    os.chmod(tmp_path, 0o700)
    _write(tmp_path / REF, 0o600)
    with pytest.raises(credentials.CredentialError):
        credentials.load_secret(tmp_path, REF, expected_uid=os.getuid() + 1)
    other = 'cred_' + 'e' * 32
    (tmp_path / other).symlink_to(tmp_path / REF)
    with pytest.raises(credentials.CredentialError):
        credentials.load_secret(tmp_path, other, expected_uid=os.getuid())
    big = 'cred_' + 'f' * 32
    (tmp_path / big).write_text('x' * 70000)
    os.chmod(tmp_path / big, 0o600)
    with pytest.raises(credentials.CredentialError):
        credentials.load_secret(tmp_path, big, expected_uid=os.getuid())
    os.chmod(tmp_path, 0o755)
    with pytest.raises(credentials.CredentialError):
        credentials.load_secret(tmp_path, REF, expected_uid=os.getuid())


@pytest.mark.parametrize('ref', ['../x', '/etc/shadow', 'cred_x/../../y', '', 'CRED_' + 'A' * 32])
def test_credential_refs_are_opaque_ids(tmp_path, ref):
    with pytest.raises(credentials.CredentialError):
        credentials.load_secret(tmp_path, ref, expected_uid=os.getuid())


def test_redaction_removes_secrets_from_any_text():
    text = f'Authorization: token {SECRET} failed'
    assert SECRET not in credentials.redact(text, [SECRET])
    assert '[REDACTED]' in credentials.redact(text, [SECRET])


def test_github_port_never_returns_or_logs_the_token(tmp_path):
    os.chmod(tmp_path, 0o700)
    _write(tmp_path / REF, 0o600)
    seen = {}

    def transport(url, headers, timeout):
        seen['url'], seen['auth'] = url, headers['Authorization']
        raise OSError('boom ' + headers['Authorization'])

    port = ops.GitHubPort(tmp_path, expected_uid=os.getuid(), transport=transport)
    github = dict(api='https://api.github.com', repo='example/app', runner_id=7, credential_ref=REF)
    with pytest.raises(ops.ActivityUnknown) as exc:
        port.runner(github)
    assert seen['url'] == 'https://api.github.com/repos/example/app/actions/runners/7'
    assert SECRET in seen['auth']
    assert SECRET not in str(exc.value)


def test_github_port_parses_runner_status(tmp_path):
    os.chmod(tmp_path, 0o700)
    _write(tmp_path / REF, 0o600)
    port = ops.GitHubPort(tmp_path, expected_uid=os.getuid(),
                          transport=lambda url, headers, timeout: (200, json.dumps(dict(id=7, status='offline', busy=False)).encode()))
    got = port.runner(dict(api='https://api.github.com', repo='example/app', runner_id=7, credential_ref=REF))
    assert got['status'] == 'offline' and got['busy'] is False
    port = ops.GitHubPort(tmp_path, expected_uid=os.getuid(), transport=lambda url, headers, timeout: (403, b'{}'))
    with pytest.raises(ops.ActivityUnknown):
        port.runner(dict(api='https://api.github.com', repo='example/app', runner_id=7, credential_ref=REF))


def test_github_endpoint_is_allowlisted():
    with pytest.raises(ValueError):
        ops.load_policy(dict(version=1, host_id='00000000-0000-4000-8000-0000000000aa', management_operations=False,
            clients=[dict(uid=1000)], targets=[dict(id='service_' + 'a' * 32, host_id='00000000-0000-4000-8000-0000000000aa',
            unit='gh-runner-canary@1.service', canary=True, description_contains='x',
            fragment_path='/etc/systemd/system/gh-runner-canary@.service', operations=['diagnose', 'service_start'],
            github=dict(api='http://169.254.169.254', repo='example/app', runner_id=7, credential_ref=REF))]))


def test_probe_key_forced_command_refuses_arbitrary_commands(tmp_path):
    spec = probe_ssh.ProbeSpec(platform='linux', services={'service_' + 'a' * 32: 'gh-runner-canary@1.service'},
                               build_volume='/')
    script = tmp_path / 'probe.py'
    script.write_text(probe_ssh.render_script(spec))
    marker = tmp_path / 'pwned'
    for command in ('id', f'touch {marker}', 'probe-v1; touch ' + str(marker), '/bin/sh -c id',
                    'systemctl restart gh-runner-canary@1.service', 'probe-v1 --extra', ''):
        env = dict(PATH='/usr/bin:/bin', SSH_ORIGINAL_COMMAND=command)
        proc = subprocess.run([sys.executable, '-I', str(script)], env=env, capture_output=True, timeout=30)
        assert proc.returncode == 64, (command, proc.returncode)
        assert proc.stdout == b''
    assert not marker.exists()


def test_probe_authorized_key_is_restricted_forced_command():
    line = authorization.probe_authorized_key('ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample probe',
                                              '/usr/bin/python3', '/usr/local/lib/hermes-runners-probe/probe.py')
    assert line.startswith('restrict,command="/usr/bin/python3 -I /usr/local/lib/hermes-runners-probe/probe.py" ')
    for unsafe in ('/home/me/probe.py', '/usr/local/lib/x;id', 'relative.py'):
        with pytest.raises(ValueError):
            authorization.probe_authorized_key('ssh-ed25519 AAAA x', '/usr/bin/python3', unsafe)


def test_polkit_rule_grants_only_start_of_listed_canary_units():
    pol = ops.load_policy(dict(version=1, host_id='00000000-0000-4000-8000-0000000000aa', management_operations=True,
        clients=[dict(uid=1000)], targets=[
            dict(id='service_' + 'a' * 32, host_id='00000000-0000-4000-8000-0000000000aa', unit='gh-runner-canary@1.service',
                 canary=True, description_contains='x', fragment_path='/etc/systemd/system/gh-runner-canary@.service',
                 operations=['diagnose', 'service_start'],
                 github=dict(api='https://api.github.com', repo='example/app', runner_id=7, credential_ref=REF)),
            dict(id='service_' + 'b' * 32, host_id='00000000-0000-4000-8000-0000000000aa', unit='gh-runner@1.service',
                 canary=False, description_contains='y', fragment_path='/etc/systemd/system/gh-runner@.service',
                 operations=['diagnose'])]))
    rule = authorization.polkit_rule(pol, broker_user='hermes-runners-broker')
    assert '"gh-runner-canary@1.service"' in rule
    assert 'gh-runner@1.service' not in rule
    assert 'subject.user == "hermes-runners-broker"' in rule
    assert 'verb == "start"' in rule
    for forbidden in ('restart', 'stop', 'reboot', 'power', '*', 'indexOf', 'match('):
        assert forbidden not in rule
    empty = authorization.polkit_rule(ops.load_policy(dict(version=1, host_id='00000000-0000-4000-8000-0000000000aa',
        management_operations=False, clients=[dict(uid=1000)], targets=[])), broker_user='hermes-runners-broker')
    assert 'polkit.Result.YES' not in empty


def test_broker_unit_runs_as_separate_identity_from_root_owned_code():
    unit = (ROOT / 'templates/broker.service').read_text()
    for directive in ('User=hermes-runners-broker', 'Group=hermes-runners-broker', 'NoNewPrivileges=true',
                      'ProtectSystem=strict', 'ProtectHome=true', 'PrivateTmp=true', 'UMask=0077',
                      'CapabilityBoundingSet=', 'RuntimeDirectory=hermes-runners-broker',
                      'StateDirectory=hermes-runners-broker'):
        assert directive in unit, directive
    exec_line = next(l for l in unit.splitlines() if l.startswith('ExecStart='))
    assert '/usr/local/lib/hermes-runners-broker/' in exec_line
    assert '/home' not in exec_line and ' -I ' in exec_line


def test_install_check_refuses_agent_writable_policy_or_code(tmp_path):
    good = tmp_path / 'policy.json'
    good.write_text('{}')
    os.chmod(good, 0o640)
    st = good.stat()
    # Owned by the agent (non-root) -> the agent could rewrite policy: refuse.
    problems = authorization.boundary_problems(code_dir=tmp_path, policy_path=good, credential_dir=tmp_path,
                                               broker_uid=st.st_uid + 1, agent_uid=st.st_uid)
    assert any('policy' in p for p in problems)
    assert any('code' in p for p in problems)
    os.chmod(good, 0o666)
    problems = authorization.boundary_problems(code_dir=tmp_path, policy_path=good, credential_dir=tmp_path,
                                               broker_uid=st.st_uid + 1, agent_uid=st.st_uid)
    assert any('writable' in p for p in problems)


def test_agent_tool_output_never_contains_credentials():
    result = ops.untrusted('token=' + SECRET, secrets=[SECRET])
    assert SECRET not in json.dumps(result)

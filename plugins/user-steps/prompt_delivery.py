"""Canonical decisions enter the existing serve process's owning TUI runtime.

The plugin REST router and /api/ws share a process. Never discover another port,
resume a session or create a competing writer. Core verifies exact ownership and
persists admission plus user row atomically; a disconnected/cold owner fails closed.
"""
import hashlib
import json
import sys


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


class PromptAdmission:
    def __init__(self, *, rpc=None):
        self.rpc = rpc

    def _call(self, owner, method, params):
        if self.rpc is not None:
            return self.rpc(method, params)
        from hermes_cli.install_identity import get_install_id
        from hermes_cli.profiles import current_profile_name
        if (owner['gatewayId'] != f'install:{get_install_id()}' or
                owner['profile'] != current_profile_name()):
            raise RuntimeError('Prompt owner differs from authenticated runtime')
        # Importing server in an agent process would manufacture a new empty runtime.
        # Only the already-loaded serve runtime can admit this operation.
        server = sys.modules.get('tui_gateway.server')
        if server is None or method not in server._methods:
            raise RuntimeError('Owning runtime requires durable prompt admission support')
        response = server.handle_request({'jsonrpc': '2.0', 'id': params['operation_id'],
                                          'method': method, 'params': params})
        if not isinstance(response, dict) or 'error' in response:
            error = (response or {}).get('error', {})
            raise RuntimeError(error.get('message', 'Owning runtime did not acknowledge admission'))
        return response['result']

    @staticmethod
    def _params(op):
        text = ('Canonical user decision. Act only on the frozen revision below. '
                'Revise means prepare a replacement plan ONLY; keep implementation blocked. '
                'Approve permits only this exact scope and limits. permissionRequired items '
                'need independent owner permission; no GPU permission is granted here. '
                'Never repeat an operation already executed.\n' +
                encode({'operationId': op['operationId'], **op['intent']}))
        sid = op['owner']['storedSessionId']
        digest = hashlib.sha256(encode({'stored_session_id': sid, 'text': text}).encode()).hexdigest()
        return dict(profile=op['owner']['profile'], stored_session_id=sid,
                    operation_id=op['operationId'], text=text, payload_digest=digest)

    @staticmethod
    def _matches(params, receipt):
        return (isinstance(receipt, dict) and receipt.get('status') == 'accepted'
                and receipt.get('dispatch') != 'failed'
                and all(receipt.get(k) == params[k] for k in
                        ('profile', 'stored_session_id', 'operation_id', 'payload_digest'))
                and type(receipt.get('user_row_id')) is int and receipt['user_row_id'] > 0
                and isinstance(receipt.get('message_uid'), str) and bool(receipt['message_uid']))

    def deliver(self, op):
        if op['intent']['effect'] == 'decline':
            raise RuntimeError('Decline must never wake execution')
        params = self._params(op)
        query = {k: v for k, v in params.items() if k != 'text'}
        receipt = self._call(op['owner'], 'prompt.admission.get', query)
        if receipt.get('status') == 'absent':
            receipt = self._call(op['owner'], 'prompt.admit', params)
        if receipt.get('dispatch') == 'failed':
            raise RuntimeError('Decision accepted but not started; runtime dispatch failed. It will not be replayed.')
        if not self._matches(params, receipt):
            raise RuntimeError('Exact prompt admission receipt mismatch')
        return {'destination': 'owning_runtime', 'admission': receipt, 'executionStarted': False,
                'terminalCancelled': receipt.get('dispatch') == 'cancelled'}

    def readback(self, op, receipt):
        params = self._params(op)
        if receipt.get('destination') != 'owning_runtime' or not self._matches(params, receipt.get('admission')):
            return False
        if (receipt.get('terminalCancelled', False) is not
                (receipt['admission'].get('dispatch') == 'cancelled')):
            return False
        query = {k: v for k, v in params.items() if k != 'text'}
        authoritative = self._call(op['owner'], 'prompt.admission.get', query)
        return self._matches(params, authoritative) and authoritative == receipt['admission']

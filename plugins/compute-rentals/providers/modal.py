"""Pinned Modal SDK inventory RPCs, executed only inside a broker subprocess."""
import asyncio
import json
import os
import re
from .common import machine, result
from .runpod import BrokerCollector, iso, scalar


def sandbox_hardware(sb):
    resource = sb.get('resource_info') or {}
    parts = []
    for field, divisor, unit in [('milli_cpu', 1000, 'physical CPU cores'), ('memory_mb', 1024, 'GiB memory')]:
        value = (resource.get(field) or {}).get('value')
        if isinstance(value, (int, float)) and value > 0:
            parts.append('%g %s' % (value / divisor, unit))
    gpu = scalar(resource.get('gpu_type'), '')
    if gpu:
        count = (sb.get('task_info') or {}).get('gpu_config', {}).get('count')
        parts.append('%s × %s' % (scalar(count, '?') if count else '?', gpu))
    return '; '.join(parts) + ' (requested, not measured usage)' if parts else 'unknown'


class ModalCollector(BrokerCollector):
    provider = 'modal'
    scope = 'Authenticated Modal workspace; all environments; task containers and both sandbox generations (apps are not machines)'

    def __init__(self, rpc=None, max_pages=10, refresh_timeout=45, config=None):
        super().__init__(config)
        self.rpc, self.max_pages = rpc, max_pages
        self.refresh_timeout = refresh_timeout

    def collect(self):
        if self.rpc is None:
            return self.broker_collect()
        return asyncio.run(self._collect())

    async def _collect(self):
        errors, rows = [], []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.refresh_timeout
        async def call(name, **kw):
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError('Modal inventory deadline')
            return await asyncio.wait_for(self.rpc(name, **kw), min(15, remaining))
        try:
            identity = await call('TokenInfoGet')
            account = scalar(identity['workspace_id'])
            workspace = scalar(identity.get('workspace_name'), '')
            console = 'https://modal.com/apps/' + workspace if re.fullmatch(r'[a-zA-Z0-9_-]+', workspace) else 'https://modal.com/apps'
            envs = (await call('EnvironmentList'))['items']
            if not isinstance(envs, list) or len(envs) > 100:
                raise ValueError()
        except Exception:
            return result(self.provider, [], self.scope, ['Modal workspace/environment read unavailable'])
        for env in envs:
            environment = scalar(env.get('name'), '')
            if not environment:
                errors.append('Modal environment schema unavailable'); continue
            tasks, sandboxes = [], {}
            try:
                tasks = (await call('TaskList', environment_name=environment))['tasks']
                if not isinstance(tasks, list) or len(tasks) > 10000:
                    raise ValueError()
            except Exception:
                tasks = []
                errors.append('Modal task inventory incomplete')
            for version in ('SandboxList', 'SandboxListV2'):
                before = 0
                try:
                    for _ in range(self.max_pages):
                        batch = (await call(version, environment_name=environment, include_finished=False, before_timestamp=before))['sandboxes']
                        if not isinstance(batch, list) or len(batch) > 10000:
                            raise ValueError()
                        if not batch:
                            break
                        for sb in batch:
                            if not isinstance(sb.get('id'), str) or not sb['id']:
                                raise ValueError()
                            sandboxes[sb['id']] = sb
                        cursor = float(batch[-1]['created_at'])
                        if cursor <= 0 or (before and cursor >= before):
                            errors.append('Modal sandbox pagination did not progress'); break
                        # Timestamp-only cursors can omit rows sharing a boundary time.
                        if sum(float(sb.get('created_at',0)) == cursor for sb in batch) > 1:
                            errors.append('Modal sandbox timestamp tie: coverage uncertain')
                        before = cursor
                    else:
                        errors.append('Modal sandbox pagination cap reached')
                except Exception:
                    errors.append('Modal ' + version + ' inventory incomplete')
            underlying = set()
            for sb in sandboxes.values():
                info = sb.get('task_info') or {}
                if info.get('id'):
                    underlying.add(info['id'])
                elif tasks:
                    errors.append('Modal sandbox/task overlap unknown')
                rows.append(machine(self.provider, sb['id'], account, 'global',
                    display_name=scalar(sb.get('name'), sb['id']), status='running' if info.get('started_at',0) > 0 else 'unknown',
                    hardware=sandbox_hardware(sb),
                    started_at=iso(info.get('started_at')), currency='USD',
                    rate_note='Actual usage, placement and price modifiers unknown; no current burn estimate',
                    state_detail='Environment: ' + environment + '; active sandbox listing; runtime location unknown',
                    resource_kind='sandbox', console_url=console))
            for task in tasks:
                ident = task.get('task_id')
                if not isinstance(ident,str) or not ident:
                    errors.append('Modal task schema unavailable'); continue
                if ident in underlying:
                    continue
                rows.append(machine(self.provider, ident, account, 'global',
                    display_name=ident, status='running' if task.get('started_at',0) > 0 else 'starting',
                    started_at=iso(task.get('started_at')), currency='USD',
                    rate_note='TaskList does not report hardware, usage or current hourly rate',
                    state_detail='Environment: ' + environment, resource_kind='serverless_worker', console_url=console))
        return result(self.provider, rows, self.scope, sorted(set(errors)))


async def sdk_inventory():
    # Importing the SDK never creates applications or invokes user code.
    import modal
    from modal.client import _Client
    from modal_proto import api_pb2 as p
    from google.protobuf.empty_pb2 import Empty
    from google.protobuf.json_format import MessageToDict
    if modal.__version__ != '1.6.1':
        raise RuntimeError('Unsupported SDK version')
    client = await _Client.from_env()
    allowed = {'TokenInfoGet':p.TokenInfoGetRequest, 'EnvironmentList':Empty,
               'TaskList':p.TaskListRequest, 'SandboxList':p.SandboxListRequest,
               'SandboxListV2':p.SandboxListRequest}
    async def rpc(name, **kw):
        request = allowed[name](**kw)
        metadata = []
        if name == 'SandboxListV2':
            token = await client._auth_token_manager.get_token()
            metadata = [('x-modal-auth-token', token)]
        response = await getattr(client._stub, name)(request, metadata=metadata)
        # This conversion is memory-only; ONLY explicit normalization fields escape.
        return MessageToDict(response, preserving_proto_field_name=True, always_print_fields_with_no_presence=True)
    return await ModalCollector(rpc=rpc)._collect()


def build_collector(config=None):
    return ModalCollector(config=config).collect


if __name__ == '__main__':
    try:
        if not os.environ.get('MODAL_TOKEN_ID') or not os.environ.get('MODAL_TOKEN_SECRET'):
            out = result('modal', [], ModalCollector.scope, ['Credential unavailable'], True)
        else:
            out = asyncio.run(asyncio.wait_for(sdk_inventory(), 55))
    except Exception:
        out = result('modal', [], ModalCollector.scope, ['Modal SDK inventory unavailable'])
    print(json.dumps(out))

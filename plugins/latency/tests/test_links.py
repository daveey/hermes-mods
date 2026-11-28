"""Owner-qualified navigation contracts; fixtures contain only public examples."""
import importlib
import json
import time
from unittest.mock import patch

from test_api import API, api


class Links(API):
    def test_series_ledger_and_chip_preserve_remote_owner(self):
        attrs = dict(card='card-1', profile='research', board='work')
        value = dict(metric='ui.card_read', start=time.time()-10, dur_ms=9000,
                     host='desktop', gateway='remote-gateway', attrs=attrs)
        response = self.client.post('/v1/spans', json={'spans': [value]})
        self.assertEqual(response.status_code, 200, response.text)
        expected = dict(kind='card', id='card-1', gateway='remote-gateway', profile='research', board='work')
        series = self.client.get('/v1/series?metric=ui.card_read').json()['metrics'][0]
        self.assertEqual(series['slowest'][0]['link'], expected)
        ledger = self.client.get('/v1/ledger').json()
        row = next(r for g in ledger['groups'] for r in g['rows'] if r['id'] == 'ui.card_read')
        self.assertEqual(row['slowest'][0]['link'], expected)
        self.assertEqual(ledger['chip']['worst'][0]['slowest'][0]['link'], expected)

    def test_links_fail_closed_and_source_aliases(self):
        links = importlib.import_module(api.pkg().__name__+'.ledger')
        sources = {'github': {alias: {'repo': 'example/'+alias} for alias in ('core', 'mods', 'client')}}
        for alias in sources['github']:
            for attr, kind, path in [('pr', 'pr', 'pull'), ('run', 'run', 'actions/runs')]:
                self.assertEqual(links.sample_link({'attrs': {attr: '12'}}, {'source': 'github.'+alias}, sources),
                                 {'kind': kind, 'url': f'https://github.com/example/{alias}/{path}/12'})
        for sample in [
            {'gateway': 'owner', 'attrs': {'session': 's'}},
            {'gateway': 'unknown', 'attrs': {'session': 's', 'profile': 'default'}},
            {'gateway': 'owner', 'attrs': {'card': 'c', 'profile': 'default'}},
            {'gateway': 'owner', 'attrs': {'session': 's', 'profile': '../private'}},
        ]:
            self.assertIsNone(links.sample_link(sample, {'source': 'state'}, sources))
            self.assertNotIn('link', links.enrich({'source': 'state', 'slowest': [{**sample, 'link': {'url': 'bad'}}]}, sources)['slowest'][0])
        for repo in ['https://example.com/repo', 'example/repo?token=bad', '../repo']:
            self.assertIsNone(links.sample_link({'attrs': {'pr': '12'}}, {'source': 'github.core'}, {'github': {'core': {'repo': repo}}}))
        for number in ['0', '-1', '1/evil', '01']:
            self.assertIsNone(links.sample_link({'attrs': {'pr': number}}, {'source': 'github.core'}, sources))
        self.assertIsNone(links.sample_link({'attrs': {'pr': '12'}}, {'source': 'github.other'}, sources))
        self.assertEqual(links.sample_link({'gateway': 'owner', 'attrs': {'session': 's', 'profile': 'default'}}, {}, {}),
                         dict(kind='session', id='s', gateway='owner', profile='default'))

    def test_github_mapping_in_production_build_and_bad_config(self):
        _, definitions, store, _ = api.services()
        definition = next(d for d in definitions.registry(self.root) if d['source'] == 'github.mods')
        store.ingest([dict(id='github-pr', metric=definition['id'], start=time.time()-10, dur_ms=100,
                           host='collector', gateway='owner', attrs={'pr': '12'})])
        (self.root/'latency').mkdir()
        config = self.root/'latency/sources.json'
        config.write_text(json.dumps({'github': {'mods': {'repo': 'example/mods'}}}))
        row = api.build('7d', definition['id'])['metrics'][0]
        self.assertEqual(row['slowest'][0]['link'], dict(kind='pr', url='https://github.com/example/mods/pull/12'))
        for bad in ['{', '[]', '{"github": null}']:
            config.write_text(bad)
            self.assertNotIn('link', api.build('7d', definition['id'])['metrics'][0]['slowest'][0])

    def test_sampler_passes_only_explicit_board_provenance(self):
        collectors = importlib.import_module(api.pkg().__name__+'.collectors')
        (self.root/'latency').mkdir()
        local = self.root/'kanban.db'
        local.touch()
        external = self.root/'other.db'
        external.touch()
        (self.root/'state.db').touch()
        config = {'gateway': 'owner', 'profile': 'default', 'boards': [
            {'path': str(local), 'board': 'main'}, str(external)]}
        (self.root/'latency/sources.json').write_text(json.dumps(config))
        with patch.object(collectors, 'collect_state', return_value=0) as state, patch.object(collectors, 'collect_board', return_value=0) as board:
            self.sampler.cycle(self.root, include_github=False)
        self.assertEqual(state.call_args.args[-1], 'default')
        calls = {c.args[0]: c.args[-3:] for c in board.call_args_list}
        self.assertEqual(calls[local.resolve()], ('owner', 'default', 'main'))
        self.assertEqual(calls[external.resolve()], ('unknown', None, None))
        rows = list(collectors.board_samples([{'id': 'c'}], [
            dict(id=1, task_id='c', kind='created', created_at=1),
            dict(id=2, task_id='c', kind='claimed', created_at=2)],
            [dict(id='queue', start='created', end='claimed')], gateway='owner', profile='default', board='main'))
        self.assertEqual(rows[0]['attrs'], dict(card='c', profile='default', board='main'))

    def test_state_projection_and_otlp_preserve_owner_identifiers(self):
        collectors = importlib.import_module(api.pkg().__name__+'.collectors')
        rows = [dict(id=1, session_id='s', role='user', content='hello', timestamp=1),
                dict(id=2, session_id='s', role='assistant', content='done', timestamp=2, finish_reason='stop')]
        result = list(collectors.state_samples(rows, [dict(id='first', start='typed_user', end='first_assistant')],
                                              gateway='owner', profile='research'))[0]
        self.assertEqual(result['attrs'], dict(session='s', profile='research'))
        now = time.time()-10
        value = dict(metric='ui.card_read', start=now, dur_ms=1, host='desktop', gateway='owner',
                     attrs=dict(profile='research', board='work', card='c'))
        spans = importlib.import_module(api.pkg().__name__+'.spans')
        otel = importlib.import_module(api.pkg().__name__+'.otel')
        normalized = spans.validate_span(value)
        body = otel.to_otlp([normalized])
        response = self.client.post('/v1/otlp/traces', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        sample = self.client.get('/v1/series?metric=ui.card_read').json()['metrics'][0]['slowest'][0]
        self.assertEqual(sample['link'], dict(kind='card', id='c', gateway='owner', profile='research', board='work'))

    def test_owner_attributes_remain_privacy_restricted(self):
        value = dict(metric='ui.card_read', start=time.time()-10, dur_ms=1,
                     host='desktop', gateway='owner', attrs={'profile': '../private', 'board': 'main'})
        self.assertEqual(self.client.post('/v1/spans', json={'spans': [value]}).status_code, 400)
        value['attrs'] = {'profile': 'default', 'board': 'main', 'title': 'not-admitted'}
        self.assertEqual(self.client.post('/v1/spans', json={'spans': [value]}).status_code, 400)

"""Offline worker/runner selector contract; no credentials or model requests."""
import contextlib
import io
import json
import os
import unittest
from unittest.mock import Mock, patch

import importlib.util
from pathlib import Path

# Existing eval tests replace their shared module's runner with a no-paid stub.
# Load an isolated copy so test discovery order cannot replace this contract.
_spec = importlib.util.spec_from_file_location('coach_eval_selector_contract', Path(__file__).with_name('coach_eval.py'))
assert _spec is not None and _spec.loader is not None
ce = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ce)
import coach_llm_worker as worker

build_or = worker.build_openrouter_backend


class SelectorContract(unittest.TestCase):
    def test_startup_without_model_does_not_resolve_config_model_with_provider_pin(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(worker, 'build_backend') as build, \
                patch.object(worker, 'serve', return_value=0), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(worker.main(), 0)
        build.assert_not_called()
        self.assertTrue(json.loads(out.getvalue())['ready'])

    def test_startup_selector_defaults_auto_but_explicit_provider_is_preserved(self):
        for env, expected in [({'COACH_WORKER_MODEL': 'class:fast'}, 'auto'),
                              ({'COACH_WORKER_MODEL': 'claude-sonnet-5-5', 'COACH_EVAL_PROVIDER': 'anthropic'}, 'anthropic')]:
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True), \
                    patch.object(worker, 'build_backend', return_value=(Mock(), {})) as build, \
                    patch.object(worker, 'serve', return_value=0), contextlib.redirect_stdout(io.StringIO()):
                worker.main()
                build.assert_called_once_with(expected, env['COACH_WORKER_MODEL'])

    def test_invalid_explicit_selector_fails_visibly(self):
        error = 'Class selectors require provider auto; use a concrete model to pin a provider'
        with patch.dict(os.environ, {'COACH_WORKER_MODEL': 'class:fast', 'COACH_EVAL_PROVIDER': 'anthropic'}, clear=True), \
                patch.object(worker, 'build_backend', side_effect=ValueError(error)) as build, \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(worker.main(), 2)
        build.assert_called_once_with('anthropic', 'class:fast')
        self.assertEqual(json.loads(out.getvalue())['fatal'], error)

    def test_build_backend_uses_resolved_model_and_validates_selector_pin(self):
        from types import ModuleType, SimpleNamespace
        import sys
        def resolve(requested, target_model):
            if target_model.startswith('class:') and requested != 'auto':
                raise ValueError('Class selectors require provider auto; use a concrete model to pin a provider')
            return {'model': 'gpt-6-astra', 'provider': 'openai-codex', 'api_mode': 'codex_responses',
                    'api_key': 'offline', 'base_url': 'https://example.com'}
        runtime = ModuleType('hermes_cli.runtime_provider')
        runtime.resolve_runtime_provider = Mock(side_effect=resolve)
        pricing = ModuleType('agent.usage_pricing')
        pricing.get_pricing_entry = Mock(return_value=SimpleNamespace(input_cost_per_million=0,
            output_cost_per_million=0, request_cost=0, source='offline', pricing_version='test'))
        sdk = ModuleType('openai')
        sdk.OpenAI = Mock()
        with patch.dict(sys.modules, {'hermes_cli.runtime_provider': runtime, 'agent.usage_pricing': pricing, 'openai': sdk}):
            for provider, model in [('auto', 'class:fast'), ('openai-codex', 'gpt-6-astra')]:
                be, info = worker.build_backend(provider, model)
                self.assertEqual((be.resolved_model, info['provider']), ('gpt-6-astra', 'openai-codex'))
                runtime.resolve_runtime_provider.assert_called_with(requested=provider, target_model=model)
            with self.assertRaisesRegex(ValueError, 'Class selectors require provider auto'):
                worker.build_backend('anthropic', 'class:fast')

    def test_openrouter_backend_is_explicit_env_keyed_and_priced_from_the_model_list(self):
        from types import ModuleType
        import sys
        listing = {'data': [{'id': 'recursive/flint-v1.0:free', 'pricing': {'prompt': '0', 'completion': '0'}},
                            {'id': 'vendor/paid', 'pricing': {'prompt': '0.000003', 'completion': '0.000015',
                                                              'input_cache_read': '0.0000003'}}]}
        sdk = ModuleType('anthropic')
        sdk.Anthropic = Mock()
        runtime = ModuleType('hermes_cli.runtime_provider')
        runtime.resolve_runtime_provider = Mock(side_effect=AssertionError('openrouter must not use the resolver'))
        mods = {'anthropic': sdk, 'hermes_cli.runtime_provider': runtime}
        with patch.dict(sys.modules, mods), patch.dict(os.environ, {'OPENROUTER_API_KEY': 'k-offline'}, clear=True), \
                patch.object(worker, 'build_openrouter_backend',
                             side_effect=lambda m, provider: build_or(m, fetch=lambda: listing, provider=provider)) as routed:
            be, info = worker.build_backend('openrouter', 'recursive/flint-v1.0:free')
        routed.assert_called_once_with('recursive/flint-v1.0:free', provider='openrouter')
        sdk.Anthropic.assert_called_once_with(auth_token='k-offline', base_url='https://openrouter.ai/api',
                                              timeout=300, max_retries=0)
        self.assertEqual((be.resolved_model, be.provider, be.prefix, info),
                         ('recursive/flint-v1.0:free', 'openrouter', '', {'provider': 'openrouter', 'oauth': False}))
        free = be.price('recursive/flint-v1.0:free')
        self.assertEqual((free['input'], free['output'], free['request']), (0.0, 0.0, 0.0))  # $0 is a real price
        paid = be.price('vendor/paid')
        self.assertEqual([round(paid[k], 6) for k in ('input', 'output', 'cache_read')], [3.0, 15.0, 0.3])
        self.assertEqual(be.price('vendor/unlisted'), {'error': 'no price for vendor/unlisted'})

    def test_nous_gateway_uses_its_own_key_and_base(self):
        from types import ModuleType
        import sys
        sdk = ModuleType('anthropic')
        sdk.Anthropic = Mock()
        listing = {'data': [{'id': 'qwen/q', 'pricing': {'prompt': '0.0000001', 'completion': '0.0000009'}}]}
        with patch.dict(sys.modules, {'anthropic': sdk}), \
                patch.dict(os.environ, {'NOUS_API_KEY': 'n-offline', 'OPENROUTER_API_KEY': 'wrong'}, clear=True):
            be, info = build_or('qwen/q', fetch=lambda: listing, provider='nous')
        sdk.Anthropic.assert_called_once_with(auth_token='n-offline', base_url='https://inference-api.nousresearch.com',
                                              timeout=300, max_retries=0)
        self.assertEqual((be.provider, info['provider']), ('nous', 'nous'))
        price = be.price('qwen/q')
        self.assertEqual(([round(price[k], 6) for k in ('input', 'output')], price['source']),
                         ([0.1, 0.9], 'nous models api'))
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'x'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'NOUS_API_KEY.*user/nous'):
                worker.build_backend('nous', 'qwen/q')

    def test_openrouter_backend_refuses_without_a_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'OPENROUTER_API_KEY'):
                worker.build_backend('openrouter', 'recursive/flint-v1.0:free')

    def test_runner_selector_auto_and_explicit_pins(self):
        runner = ce.DirectRunner.__new__(ce.DirectRunner)
        runner.provider = 'anthropic'
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(runner.provider_for('class:fast', True), 'auto')
            self.assertEqual(runner.provider_for('class:fast', False), 'auto')
            self.assertEqual(runner.provider_for('claude-sonnet-5-5', True), 'anthropic')
        with patch.dict(os.environ, {'COACH_JUDGE_PROVIDER': 'anthropic'}, clear=True):
            self.assertEqual(runner.provider_for('class:fast', True), 'anthropic')

    def test_worker_prices_resolved_model_and_caches_per_model(self):
        builds = []
        def build(provider, model):
            builds.append((provider, model))
            be = Mock()
            be.resolved_model = 'claude-sonnet-5-5' if model == 'class:fast' else model
            be.provider = 'anthropic'
            be.price.side_effect = lambda m: {'input': 1, 'output': 2, 'priced_model': m}
            be.call.side_effect = lambda req: {'text': req['model']}
            return be, {}
        requests = [{'op': 'price', 'model': 'class:fast', 'provider': 'auto'},
                    {'op': 'call', 'model': 'class:fast', 'provider': 'auto'},
                    {'op': 'price', 'model': 'claude-opus-5-5', 'provider': 'auto'}]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            worker.serve('anthropic', {}, build=build, lines=map(json.dumps, requests))
        rows = list(map(json.loads, out.getvalue().splitlines()))
        self.assertEqual(rows[0]['priced_model'], 'claude-sonnet-5-5')
        self.assertEqual(rows[0]['model'], 'claude-sonnet-5-5')
        self.assertEqual(rows[0]['provider'], 'anthropic')
        self.assertEqual(rows[1]['text'], 'claude-sonnet-5-5')
        self.assertEqual(len(builds), 2)

    def test_two_workers_freeze_transcript_and_dict_judge_routes(self):
        import queue
        import threading
        spec = importlib.util.spec_from_file_location('bench_selector_contract', Path(__file__).with_name('coach_model_bench.py'))
        assert spec is not None and spec.loader is not None
        bench = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', {'coach_eval': ce}):
            spec.loader.exec_module(bench)
        for prompt in ({'messages': [{'role': 'user', 'content': 'hi'}], 'tools': []},
                       {'system': 'judge', 'user': 'hi'}):
            runner = bench.TranscriptRunner.__new__(bench.TranscriptRunner)
            runner.provider, runner.preload, runner.judge_max_tokens = 'auto', [], 30
            runner.prices, runner.plock, runner.pool = {}, threading.Lock(), queue.Queue()
            sent = []
            class FakeWorker:
                def __init__(self, chosen):
                    self.chosen = chosen
                def ask(self, req):
                    if req['op'] == 'price':
                        return {'input': 1, 'output': 2, 'model': self.chosen, 'provider': 'anthropic'}
                    sent.append(req)
                    # A selector at worker two would resolve differently: forbid it.
                    assert req['model'] == 'claude-sonnet-5-5', req
                    return {'text': 'offline', 'usage': {}}
            runner.pool.put(FakeWorker('claude-sonnet-5-5'))
            runner.pool.put(FakeWorker('claude-opus-5-5'))
            with patch.object(bench, 'compose', return_value='system'), patch.dict(os.environ, {}, clear=True):
                req = runner.prepare(prompt, model='class:fast', ignore_rules=True)
                runner.dispatch(req)
            self.assertEqual((sent[0]['model'], sent[0]['provider']), ('claude-sonnet-5-5', 'anthropic'))

    def test_reservation_freezes_concrete_route_before_dispatch(self):
        runner = ce.DirectRunner.__new__(ce.DirectRunner)
        runner.provider = 'auto'
        runner.judge_max_tokens = 30
        runner.price = Mock(return_value={'input': 1, 'output': 2, 'model': 'claude-sonnet-5-5', 'provider': 'anthropic'})
        req = runner.prepare('judge', model='class:fast', ignore_rules=True)
        self.assertEqual((req.model, req.provider), ('claude-sonnet-5-5', 'anthropic'))
        runner._with_worker = Mock(return_value={'text': 'real wire stub', 'usage': {}})
        runner.dispatch(req)
        sent = runner._with_worker.call_args.args[0]
        self.assertEqual((sent['model'], sent['provider']), (req.model, req.provider))


if __name__ == '__main__':
    unittest.main()

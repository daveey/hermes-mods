"""Structured-only same-message confirmation binding through Desktop's parser.

The shipped Node bundle uses the actual Desktop preprocessing and directive
modules. Missing runtime, source drift and parse doubt mean no recovered scope.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess


def structured_confirmation(reply):
    if not isinstance(reply, str) or len(reply) > 65536:
        return None
    directory = Path(__file__).with_name('parser')
    try:
        manifest = json.loads((directory / 'manifest.json').read_text())
        core = Path(os.environ.get('HERMES_CORE_CHECKOUT') or Path.home() / '.hermes/hermes-agent')
        for entry in manifest['modules']:
            if hashlib.sha256((core / entry['path']).read_bytes()).hexdigest() != entry['sha256']:
                return None
        bundle = directory / 'scanner.mjs'
        if hashlib.sha256(bundle.read_bytes()).hexdigest() != manifest['bundle_sha256']:
            return None
        result = subprocess.run(['node', str(bundle)], input=json.dumps({'reply': reply}),
                                capture_output=True, text=True, timeout=3, check=True)
        if len(result.stdout) > 200000:
            return None
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or not isinstance(value.get('plan'), str):
            return None
        attrs = value['ask']['attrs']
        if not isinstance(attrs, dict) or not isinstance(attrs.get('t'), str):
            return None
        # Recovery is never a way to borrow another session or request's id.
        if set(attrs) - {'t', 'id', 's'}:
            return None
        source = value['ask']['source']
        if reply.count(source) != 1 or not reply.rstrip().endswith(source):
            return None
        # Freeze original bytes, not preprocessed display prose (which may hide
        # reasoning/preview markup). No removed text can silently widen scope.
        value['plan'] = reply[:reply.index(source)].rstrip()
        return value
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return None


def preceding_plan(reply, span):
    """Compatibility helper: plain prose is never an authorizing ask."""
    parsed = structured_confirmation(reply)
    return parsed['plan'] if parsed and parsed['ask']['attrs'].get('t') == span else None

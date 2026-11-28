"""Latency ledger: derived history and privacy-restricted operation spans."""
from .spans import span


def register(ctx):
    from . import sampler
    if sampler.eligible_process():
        sampler.start()
        on_unload = getattr(ctx, 'on_unload', None)
        if callable(on_unload):
            on_unload(sampler.stop)

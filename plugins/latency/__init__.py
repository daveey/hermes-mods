"""Latency ledger: derived history and privacy-restricted operation spans."""
from .spans import span


def register(ctx):
    from . import sampler, watch
    ctx.register_tool(name='latency_watch', toolset='latency', schema=watch.SCHEMA,
                      handler=watch.handle, description=watch.SCHEMA['description'])
    if sampler.eligible_process():
        sampler.start()
        on_unload = getattr(ctx, 'on_unload', None)
        if callable(on_unload):
            on_unload(sampler.stop)

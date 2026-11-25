"""LLM split: quota share vs routing share per subscription pool, and a control that sets the routing weights.

core.py does the work; dashboard/plugin_api.py is the REST API for the Desktop chip. In a long-lived server process
(gateway, Desktop serve, dashboard) the auto loop re-applies the quota share every 5 minutes while the mode is auto.
"""
import logging

log = logging.getLogger("hermes.plugins.llm-split")


def register(ctx):
    from . import core
    if core.eligible_process():
        try:
            core.start()
        except Exception:  # never break plugin loading
            log.exception("llm-split: auto loop failed to start")
        on_unload = getattr(ctx, "on_unload", None)
        if callable(on_unload):
            try:
                on_unload(core.stop)
            except Exception:
                pass
    return None

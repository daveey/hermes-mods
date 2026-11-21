"""session-repair: no hooks or tools. The scanner (scan.py) and the cron tick (run.py) run outside the agent; the
read-only findings route lives in dashboard/plugin_api.py."""


def register(ctx) -> None:  # noqa: ARG001 -- nothing to register
    return None

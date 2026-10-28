"""chrome-recycle-guard has no hooks; chrome_recycle.py is run from a systemd timer or cron."""


def register(ctx):  # pragma: no cover - nothing to register
    return None

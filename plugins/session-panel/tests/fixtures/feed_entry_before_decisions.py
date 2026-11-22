# Frozen feed() entry point from 1bce0048^ dashboard/plugin_api.py.
# Kept verbatim to exercise a real mixed-generation hot reload.
def feed():
    """The process's one feed, started on first use (its thread ticks every TICK_S until stopped)."""
    while True:
        core = _load_feed()
        with core.PROCESS_LOCK:
            if sys.modules.get(_FEED_KEY) is not core:
                continue  # a reload swapped the module (under this same lock) after we loaded it: use the new one
            if core.PROCESS_FEED is None or core.PROCESS_FEED.stopped.is_set():
                prev, new = core.PROCESS_FEED, core.Feed(CoreReaders(), emit=_emit)
                if prev is not None:  # a plugin reload stopped it: carry its revisions on, so clients see no reset
                    try:
                        new.resume(prev)
                    except Exception:  # never leave the process without a running feed: start clean (clients reset)
                        log.warning('session-panel feed: resume failed; starting a new revision space', exc_info=True)
                        new = core.Feed(CoreReaders(), emit=_emit)
                else:  # this process's first feed: carry on the one its previous process saved
                    snap = load_snapshot()
                    if snap is not None:
                        try:
                            new.restore(snap)
                            log.info('session-panel feed: restored epoch %s at rev %s (%d bundles)',
                                     new.epoch, new.rev, len(new.bundles))
                        except Exception:
                            log.warning('session-panel feed: snapshot not restored; starting a new revision space',
                                        exc_info=True)
                            new = core.Feed(CoreReaders(), emit=_emit)
                    save_snapshot(new, claim=True)  # claim it now: the process this one replaces no longer saves
                    _save_at_exit()
                core.PROCESS_FEED = new
                threading.Thread(target=_run, args=(core.PROCESS_FEED,), name='session-panel-feed', daemon=True).start()
            return core.PROCESS_FEED

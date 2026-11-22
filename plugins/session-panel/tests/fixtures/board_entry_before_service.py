# Frozen entry functions from 69f76580^ plugin_api.py, AST-unparsed.
# Exercise the real predecessor closure, not a current-loop imitation.
def feed():
    while True:
        core = _load_feed()
        with core.PROCESS_LOCK:
            if sys.modules.get(_FEED_KEY) is not core:
                continue
            if core.PROCESS_FEED is None or core.PROCESS_FEED.stopped.is_set():
                prev, new = (core.PROCESS_FEED, core.Feed(CoreReaders(), emit=_emit))
                if prev is not None:
                    try:
                        new.resume(prev)
                    except Exception:
                        log.warning('session-panel feed: resume failed; starting a new revision space', exc_info=True)
                        new = core.Feed(CoreReaders(), emit=_emit)
                else:
                    snap = load_snapshot()
                    if snap is not None:
                        try:
                            new.restore(snap)
                            log.info('session-panel feed: restored epoch %s at rev %s (%d bundles)', new.epoch, new.rev, len(new.bundles))
                        except Exception:
                            log.warning('session-panel feed: snapshot not restored; starting a new revision space', exc_info=True)
                            new = core.Feed(CoreReaders(), emit=_emit)
                    save_snapshot(new, claim=True)
                    _save_at_exit()
                core.PROCESS_FEED = new
                threading.Thread(target=_run, args=(core.PROCESS_FEED,), name='session-panel-feed', daemon=True).start()
            lane = getattr(core.PROCESS_FEED, '_decision_thread', None)
            if lane is None or not lane.is_alive():
                lane = threading.Thread(target=_run_decisions, args=(core.PROCESS_FEED,), name='session-panel-decisions', daemon=True)
                core.PROCESS_FEED._decision_thread = lane
                lane.start()
            lane = getattr(core.PROCESS_FEED, '_board_thread', None)
            if lane is None or not lane.is_alive():
                lane = threading.Thread(target=_run_board, args=(core.PROCESS_FEED, core.BoardLane), name='session-panel-board', daemon=True)
                core.PROCESS_FEED._board_thread = lane
                lane.start()
            return core.PROCESS_FEED


def _run_board(f, lane_type) -> None:
    lane = lane_type(f, CoreReaders)
    first = True
    try:
        while not f.stopped.is_set():
            if not first:
                at = lane.retry_at()
                f._board.wait(None if at is None else max(0, at - f.clock()))
                f.stopped.wait(DEBOUNCE_S)
            if f.stopped.is_set():
                break
            first = False
            lane.step()
            write_timing(f)
    finally:
        lane.close()

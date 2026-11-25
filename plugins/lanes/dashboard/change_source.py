"""Native dirty gate; timer wakes inspect memory/notifications, not board SQL."""
from __future__ import annotations

import threading


class Source:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = None
        self.value = None
        self.paths = ()
        self.fallback = False
        self.stamp = None
        self.pending = True  # only a completed capture may consume invalidation

    def _stamp(self):
        from pathlib import Path
        result = []
        for name in self.paths:
            for suffix in ('', '-wal', '-shm'):
                try:
                    s = Path(str(name) + suffix).stat()
                    result.append((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns))
                except FileNotFoundError:
                    result.append(None)
        return tuple(result)

    def _cover(self):
        assert self.events is not None
        from pathlib import Path
        directories = tuple(p for p in self.paths if Path(p).is_dir())
        if directories:
            self.events.replace(self.paths, directories=directories)
        else:
            self.events.replace(self.paths)

    def read(self, capture):
        with self.lock:
            if self.fallback:
                # Older/non-Linux cores: file metadata only on a quiet timer.
                # A changed stamp admits SQL, but the semantic result below—not
                # the write stamp—decides whether projection is necessary.
                stamp = self._stamp()
                if stamp != self.stamp:
                    self.value, self.paths = capture()
                    self.stamp = stamp  # a racing write remains trailing demand
                return self.value
            try:
                if self.events is None:
                    from hermes_cli.annotation_file_events import FileEvents
                    self.events = FileEvents()
                elif not self.events.wait(0) and not self.pending:
                    return self.value
                self.pending = True
                # Install before capture, preserving arrivals during SQL for a
                # trailing read. First capture discovers source paths, then reads
                # again with coverage installed so discovery cannot swallow a write.
                if not self.paths:
                    _, self.paths = capture()
                self._cover()
                self.value, paths = capture()
                if paths != self.paths:
                    self.paths = paths
                    self._cover()
                    self.value, _ = capture()
                self.pending = False
                return self.value
            except (ImportError, NotImplementedError, OSError):
                self.fallback = True
                if self.events is not None:
                    self.events.close()
                self.value, self.paths = capture()
                # Do not certify metadata taken after capture; leave one
                # trailing check to cover a source write during that capture.
                self.stamp = None
                return self.value

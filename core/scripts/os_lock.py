#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OS advisory file lock (fcntl / msvcrt). The kernel drops it when the process dies,
so a crashed holder never leaves a stale lock behind."""
from __future__ import annotations

import os
import time
from pathlib import Path


class Busy(Exception):
    pass


class FileLock:
    def __init__(self, path, wait_seconds: float = 0.0):
        self.path = Path(path)
        self.wait = wait_seconds
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, 'a+b')
        deadline = time.monotonic() + self.wait
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    self.fh.close()
                    self.fh = None
                    raise Busy(str(self.path)) from None
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self.fh is None:
            return False
        try:
            if os.name == 'nt':
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        finally:
            self.fh.close()
            self.fh = None
        return False

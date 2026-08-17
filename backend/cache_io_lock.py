"""Shared in-process lock for application cache readers, writers, and cleanup."""

from __future__ import annotations

import threading


CACHE_IO_LOCK = threading.RLock()

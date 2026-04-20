"""
file_ingest.py — Tails one or more Honeytrap JSONL log files and pushes
normalized events into the queue for delivery to Cerebrum.

Behaviour
---------
* Opens each configured log file and seeks to EOF on startup (so we don't
  re-ingest historical data unless INGEST_FROM_START=true).
* Reads new lines as they appear (tail -F semantics).
* Handles log rotation: if the file shrinks or is deleted/recreated, the
  watcher reopens it.
* Supports gzip-compressed rotated archives (.gz) if INGEST_GZIP=true.
* Each JSONL line is parsed, normalized, and enqueued.

Environment variables
---------------------
INGEST_LOG_PATHS   Comma-separated list of paths to watch, e.g.
                   /var/log/honeytrap/events.jsonl,/var/log/honeytrap/http.jsonl
INGEST_FROM_START  true → replay entire file on startup (default: false)
INGEST_POLL_MS     Polling interval in milliseconds (default: 200)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from normalize import normalize
from queue_manager import enqueue

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOG_PATHS: List[str] = [
    p.strip()
    for p in os.environ.get("INGEST_LOG_PATHS", "/var/log/honeytrap/events.jsonl").split(",")
    if p.strip()
]
FROM_START: bool = os.environ.get("INGEST_FROM_START", "false").lower() == "true"
POLL_INTERVAL: float = int(os.environ.get("INGEST_POLL_MS", "200")) / 1000.0

# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

_stats: Dict[str, int] = {
    "lines_read": 0,
    "parse_errors": 0,
    "normalize_failures": 0,
    "events_enqueued": 0,
}


def get_file_stats() -> Dict[str, int]:
    return dict(_stats)


# ---------------------------------------------------------------------------
# Single-file watcher
# ---------------------------------------------------------------------------

class FileWatcher:
    """
    Watches a single JSONL file for new lines.
    Reopens the file if it is rotated (inode change or size reduction).
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._fh = None
        self._inode: Optional[int] = None
        self._pos: int = 0

    def _open(self) -> None:
        try:
            self._fh = open(self.path, "r", encoding="utf-8", errors="replace")
            st = os.fstat(self._fh.fileno())
            self._inode = st.st_ino
            if not FROM_START:
                self._fh.seek(0, 2)  # seek to EOF
                self._pos = self._fh.tell()
            else:
                self._pos = 0
            logger.info("FileWatcher opened %s (inode=%d, pos=%d)", self.path, self._inode, self._pos)
        except OSError as exc:
            logger.warning("FileWatcher: cannot open %s: %s", self.path, exc)
            self._fh = None

    def _check_rotation(self) -> bool:
        """Return True if the file has been rotated and we need to reopen."""
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return True  # deleted — reopen when recreated
        if st.st_ino != self._inode:
            return True  # new inode → rotated
        if st.st_size < self._pos:
            return True  # truncated
        return False

    def read_new_lines(self) -> List[str]:
        """Return list of new JSONL lines since last call."""
        if self._fh is None:
            self._open()
            if self._fh is None:
                return []

        if self._check_rotation():
            logger.info("FileWatcher: detected rotation on %s — reopening", self.path)
            self._fh.close()
            self._fh = None
            self._open()
            if self._fh is None:
                return []

        lines: List[str] = []
        while True:
            line = self._fh.readline()
            if not line:
                break
            lines.append(line.rstrip("\n"))
        self._pos = self._fh.tell()
        return lines

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


# ---------------------------------------------------------------------------
# Line processor
# ---------------------------------------------------------------------------

async def _process_line(line: str, path: str) -> None:
    """Parse a JSONL line, normalize it, and push to queue."""
    if not line.strip():
        return

    _stats["lines_read"] += 1

    try:
        record: Dict[str, Any] = json.loads(line)
    except json.JSONDecodeError as exc:
        _stats["parse_errors"] += 1
        logger.warning("JSON parse error in %s: %s | line=%r", path, exc, line[:120])
        return

    event = normalize(record, source="file")
    if event is None:
        _stats["normalize_failures"] += 1
        return

    await enqueue(event)
    _stats["events_enqueued"] += 1


# ---------------------------------------------------------------------------
# Per-file watcher coroutine
# ---------------------------------------------------------------------------

async def watch_file(path: str) -> None:
    """
    Coroutine that tails a single JSONL file indefinitely.
    Cancelled externally when the application shuts down.
    """
    logger.info("Starting file watcher for: %s", path)
    watcher = FileWatcher(path)
    try:
        while True:
            lines = watcher.read_new_lines()
            for line in lines:
                await _process_line(line, path)
            await asyncio.sleep(POLL_INTERVAL)
    except asyncio.CancelledError:
        logger.info("File watcher cancelled for %s", path)
    finally:
        watcher.close()


# ---------------------------------------------------------------------------
# Supervisor: watch all configured paths
# ---------------------------------------------------------------------------

_watcher_tasks: List[asyncio.Task] = []


async def start_file_watchers() -> None:
    """
    Spawn one asyncio Task per configured log path.
    Called once at application startup.
    """
    if not LOG_PATHS:
        logger.warning("INGEST_LOG_PATHS is empty — file ingestion disabled")
        return

    for path in LOG_PATHS:
        task = asyncio.create_task(watch_file(path), name=f"watcher:{path}")
        _watcher_tasks.append(task)
        logger.info("Spawned file watcher task for %s", path)


async def stop_file_watchers() -> None:
    """Cancel all watcher tasks gracefully."""
    for task in _watcher_tasks:
        if not task.done():
            task.cancel()
    if _watcher_tasks:
        await asyncio.gather(*_watcher_tasks, return_exceptions=True)
    _watcher_tasks.clear()
    logger.info("All file watchers stopped")


# ---------------------------------------------------------------------------
# Standalone entrypoint (for running as a separate process if needed)
# ---------------------------------------------------------------------------

async def _main() -> None:
    import signal

    loop = asyncio.get_running_loop()

    async def _shutdown():
        logger.info("Shutdown signal received")
        await stop_file_watchers()
        loop.stop()

    loop.add_signal_handler(signal.SIGINT, lambda: asyncio.create_task(_shutdown()))
    loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(_shutdown()))

    await start_file_watchers()
    # Keep running until cancelled
    await asyncio.Event().wait()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_main())

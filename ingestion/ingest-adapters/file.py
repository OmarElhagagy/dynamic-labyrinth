"""
ingest-adapters/file.py — Honeytrap file-pusher adapter.

Roadmap reference: Alaa — "ingest-adapters/ for different pushers (file, elasticsearch, kafka)"

Handles the Honeytrap file pusher format:
  - JSONL lines written to disk by Honeytrap's file pusher plugin
  - Fields use kebab-case (src-ip, dst-port, start_time, etc.)
  - Tails files in real-time with rotation detection

Usage (standalone):
    python -m ingest-adapters.file --path /var/log/honeytrap/events.jsonl

Usage (imported):
    from ingest_adapters.file import FilePusherAdapter
    adapter = FilePusherAdapter("/var/log/honeytrap/events.jsonl")
    for event in adapter.iter_events():
        ...
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Generator, Iterator, List, Optional

logger = logging.getLogger(__name__)


class FilePusherAdapter:
    """
    Tails a Honeytrap JSONL log file and yields raw event dicts.

    Handles:
      - New lines appended to existing file
      - Log rotation (inode change or file shrink)
      - Missing file (retries until it appears)
    """

    def __init__(
        self,
        path: str,
        from_start: bool = False,
        poll_interval_ms: int = 200,
    ) -> None:
        self.path = Path(path)
        self.from_start = from_start
        self.poll_interval = poll_interval_ms / 1000.0
        self._fh = None
        self._inode: Optional[int] = None
        self._pos: int = 0

    # ------------------------------------------------------------------
    # File open / rotation detection
    # ------------------------------------------------------------------

    def _open(self) -> None:
        try:
            self._fh = open(self.path, "r", encoding="utf-8", errors="replace")
            st = os.fstat(self._fh.fileno())
            self._inode = st.st_ino
            if not self.from_start:
                self._fh.seek(0, 2)
            self._pos = self._fh.tell()
            logger.info("FilePusherAdapter opened %s (inode=%d pos=%d)", self.path, self._inode, self._pos)
        except OSError as exc:
            logger.warning("Cannot open %s: %s — will retry", self.path, exc)
            self._fh = None

    def _rotated(self) -> bool:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return True
        return st.st_ino != self._inode or st.st_size < self._pos

    # ------------------------------------------------------------------
    # Synchronous iteration (for simple use cases)
    # ------------------------------------------------------------------

    def read_new_lines(self) -> List[str]:
        """Return newly appended lines since last call."""
        if self._fh is None:
            self._open()
            if self._fh is None:
                return []
        if self._rotated():
            logger.info("Rotation detected on %s — reopening", self.path)
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

    def iter_events(self) -> Iterator[Dict[str, Any]]:
        """
        Infinite iterator that blocks between polls.
        Yields parsed event dicts.
        """
        while True:
            for line in self.read_new_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("JSON parse error: %s | line=%r", exc, line[:80])
            time.sleep(self.poll_interval)

    # ------------------------------------------------------------------
    # Async iteration
    # ------------------------------------------------------------------

    async def iter_events_async(self):
        """Async generator version — use with 'async for'."""
        while True:
            for line in self.read_new_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("JSON parse error: %s", exc)
            await asyncio.sleep(self.poll_interval)

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ---------------------------------------------------------------------------
# Adapter metadata (used by adapter registry)
# ---------------------------------------------------------------------------

ADAPTER_NAME = "file"
ADAPTER_DESCRIPTION = "Honeytrap file-pusher JSONL log tailing adapter"
SOURCE_TAG = "file"


def create(config: Dict[str, Any]) -> FilePusherAdapter:
    """Factory function used by the adapter registry."""
    return FilePusherAdapter(
        path=config["path"],
        from_start=config.get("from_start", False),
        poll_interval_ms=config.get("poll_interval_ms", 200),
    )


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent))

    parser = argparse.ArgumentParser(description="Stream Honeytrap file-pusher events")
    parser.add_argument("--path", required=True, help="JSONL log file path")
    parser.add_argument("--from-start", action="store_true", help="Replay from beginning of file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    with FilePusherAdapter(args.path, from_start=args.from_start) as adapter:
        for event in adapter.iter_events():
            print(json.dumps(event))

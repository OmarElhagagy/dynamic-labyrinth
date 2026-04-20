# honeytrap/hardened_pusher.py
"""
Hardened Honeytrap Pusher - Direct HTTP ingestion with HMAC authentication.

This replaces the JSONL file pusher. It tails Honeytrap's log file and
sends each event directly to the ingestion service with HMAC signing.

Benefits over file-based ingestion:
- No file rotation issues
- Lower latency (events delivered immediately)
- Built-in retry and backoff
- End-to-end authentication
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

INGESTION_URL = os.environ.get("INGESTION_URL", "http://ingestion:8002/ingest/honeytrap")
HMAC_SECRET = os.environ.get("HMAC_SECRET", "")
HONEYTRAP_LOG_PATH = os.environ.get("HONEYTRAP_LOG_PATH", "/var/log/honeytrap/events.jsonl")
POLL_INTERVAL_MS = int(os.environ.get("POLL_INTERVAL_MS", "100"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "5"))
BACKOFF_FACTOR = float(os.environ.get("BACKOFF_FACTOR", "1.0"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hardened-pusher")

# ---------------------------------------------------------------------------
# HMAC Signing (mirrors ingestion service)
# ---------------------------------------------------------------------------

def _body_hash(body: bytes) -> str:
    """Return lowercase hex SHA-256 of raw body bytes."""
    import hashlib
    return hashlib.sha256(body).hexdigest()


def sign_request(method: str, path: str, body: bytes, secret: str) -> Dict[str, str]:
    """
    Produce HMAC headers for ingestion service authentication.
    """
    import hmac
    import hashlib

    timestamp = str(int(time.time()))
    body_hash = _body_hash(body)
    signing_string = f"{method.upper()}\n{path}\n{timestamp}\n{body_hash}"
    signature = hmac.new(
        secret.encode("utf-8"),
        signing_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return {
        "X-DL-Timestamp": timestamp,
        "X-DL-Signature": signature,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Session (with retry)
# ---------------------------------------------------------------------------

def create_session() -> requests.Session:
    """Create requests session with retry strategy."""
    session = requests.Session()
    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# ---------------------------------------------------------------------------
# File tailer
# ---------------------------------------------------------------------------

class JSONLTailer:
    """
    Tail a JSONL file and yield new lines as they appear.
    Handles log rotation (checks inode changes).
    """

    def __init__(self, path: str):
        self.path = Path(path)
        self._fh: Optional[object] = None
        self._inode: Optional[int] = None
        self._position: int = 0

    def _open(self) -> bool:
        """Open file and seek to end (tail -F behavior)."""
        try:
            self._fh = open(self.path, "r", encoding="utf-8", errors="replace")
            import os
            st = os.fstat(self._fh.fileno())
            self._inode = st.st_ino
            self._fh.seek(0, 2)  # EOF
            self._position = self._fh.tell()
            logger.info("Opened %s (inode=%d, position=%d)", self.path, self._inode, self._position)
            return True
        except (OSError, IOError) as e:
            logger.warning("Cannot open %s: %s", self.path, e)
            self._fh = None
            return False

    def _check_rotation(self) -> bool:
        """Return True if file was rotated/truncated."""
        import os
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return True

        if st.st_ino != self._inode:
            logger.info("File rotated: inode changed %d -> %d", self._inode, st.st_ino)
            return True
        if st.st_size < self._position:
            logger.info("File truncated: size %d < position %d", st.st_size, self._position)
            return True
        return False

    def read_new_lines(self) -> List[str]:
        """Return list of new lines since last call."""
        if self._fh is None:
            if not self._open():
                return []

        if self._check_rotation():
            self.close()
            if not self._open():
                return []

        lines = []
        while True:
            line = self._fh.readline()
            if not line:
                break
            line = line.rstrip("\n")
            if line:
                lines.append(line)
        self._position = self._fh.tell()
        return lines

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None


# ---------------------------------------------------------------------------
# Event sender
# ---------------------------------------------------------------------------

class EventSender:
    """Send events to ingestion service with HMAC and retry."""

    def __init__(self, ingestion_url: str, secret: str):
        self.ingestion_url = ingestion_url.rstrip("/")
        self.secret = secret
        self.session = create_session()
        self._cache: Dict[str, Any] = {}

    def _extract_path(self, url: str) -> str:
        """Extract path from full URL."""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.path or "/"

    def send_event(self, event: Dict[str, Any]) -> bool:
        """
        Send a single event to ingestion service.
        Returns True on success, False on permanent failure.
        """
        if not self.secret or self.secret == "change-me-in-production":
            logger.error("HMAC_SECRET not set or still default - refusing to send")
            return False

        body = json.dumps(event).encode("utf-8")
        path = self._extract_path(self.ingestion_url)
        headers = sign_request("POST", path, body, self.secret)

        try:
            resp = self.session.post(
                self.ingestion_url,
                data=body,
                headers=headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            event_id = resp.json().get("event_id", "unknown")
            logger.debug("Event sent successfully: %s", event_id)
            return True
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                logger.error("HMAC authentication failed - check HMAC_SECRET")
            elif e.response.status_code == 422:
                logger.warning("Event rejected by ingestion (invalid schema): %s", event.get("type"))
            else:
                logger.warning("HTTP error %s: %s", e.response.status_code, e)
            return False
        except requests.exceptions.RequestException as e:
            logger.warning("Request failed: %s", e)
            return False

    def close(self):
        self.session.close()


# ---------------------------------------------------------------------------
# Dead letter queue (disk fallback)
# ---------------------------------------------------------------------------

class DeadLetterQueue:
    """Store failed events to disk for manual recovery."""

    def __init__(self, path: str = "/tmp/hardened_pusher_dlq.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: Dict[str, Any], reason: str = "") -> None:
        """Append failed event to dead letter queue."""
        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "event": event,
            "reason": reason,
        }
        try:
            with open(self.path, "a") as f:
                f.write(json.dumps(record) + "\n")
            logger.warning("Event written to DLQ: %s", self.path)
        except OSError as e:
            logger.error("Cannot write to DLQ: %s", e)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

class HardenedPusher:
    """Main orchestrator: tail file, send events, handle shutdown."""

    def __init__(self):
        self.tailer = JSONLTailer(HONEYTRAP_LOG_PATH)
        self.sender = EventSender(INGESTION_URL, HMAC_SECRET)
        self.dlq = DeadLetterQueue()
        self.running = True
        self.stats = {
            "lines_read": 0,
            "events_sent": 0,
            "events_failed": 0,
        }

    def _signal_handler(self, signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        self.running = False

    def run(self):
        """Main loop: poll file, send events."""
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        logger.info("Hardened Honeytrap Pusher starting")
        logger.info("Ingestion URL: %s", INGESTION_URL)
        logger.info("Log file: %s", HONEYTRAP_LOG_PATH)
        logger.info("Poll interval: %d ms", POLL_INTERVAL_MS)

        if not HMAC_SECRET or HMAC_SECRET == "change-me-in-production":
            logger.warning("HMAC_SECRET is default or empty - SECURITY RISK")

        while self.running:
            try:
                lines = self.tailer.read_new_lines()
                for line in lines:
                    self.stats["lines_read"] += 1
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as e:
                        logger.warning("Invalid JSON: %s", e)
                        self.dlq.write({"raw_line": line}, reason=f"JSON parse error: {e}")
                        continue

                    success = self.sender.send_event(event)
                    if success:
                        self.stats["events_sent"] += 1
                        logger.debug("Event sent: %s", event.get("type", "unknown"))
                    else:
                        self.stats["events_failed"] += 1
                        self.dlq.write(event, reason="Delivery failed after retries")

                # Log stats periodically
                if self.stats["lines_read"] % 100 == 0 and self.stats["lines_read"] > 0:
                    logger.info(
                        "Stats: lines=%d, sent=%d, failed=%d",
                        self.stats["lines_read"],
                        self.stats["events_sent"],
                        self.stats["events_failed"],
                    )

                time.sleep(POLL_INTERVAL_MS / 1000.0)

            except Exception as e:
                logger.exception("Unexpected error in main loop: %s", e)
                time.sleep(1)

        # Shutdown
        logger.info("Shutting down...")
        self.tailer.close()
        self.sender.close()
        logger.info(
            "Final stats: lines=%d, sent=%d, failed=%d",
            self.stats["lines_read"],
            self.stats["events_sent"],
            self.stats["events_failed"],
        )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    if not HMAC_SECRET:
        logger.error("HMAC_SECRET environment variable is required!")
        sys.exit(1)

    pusher = HardenedPusher()
    pusher.run()


if __name__ == "__main__":
    main()
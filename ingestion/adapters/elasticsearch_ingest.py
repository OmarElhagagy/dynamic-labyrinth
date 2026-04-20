# ingestion/adapters/elasticsearch_ingest.py
"""
Elasticsearch adapter for ingestion service.

Reads events from Elasticsearch indices and normalizes them
into the canonical schema for Cerebrum.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from elasticsearch import AsyncElasticsearch
from elasticsearch.exceptions import NotFoundError

# Add parent directory to path
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalize import normalize
from queue_manager import enqueue

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ELASTICSEARCH_HOSTS = os.environ.get("ELASTICSEARCH_HOSTS", "http://localhost:9200").split(",")
ELASTICSEARCH_INDEX = os.environ.get("ELASTICSEARCH_INDEX", "honeytrap-*")
ELASTICSEARCH_USERNAME = os.environ.get("ELASTICSEARCH_USERNAME", "")
ELASTICSEARCH_PASSWORD = os.environ.get("ELASTICSEARCH_PASSWORD", "")
POLL_INTERVAL_SECONDS = int(os.environ.get("ELASTICSEARCH_POLL_SECONDS", "5"))
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "1"))  # Replay last N hours on startup
BATCH_SIZE = int(os.environ.get("ELASTICSEARCH_BATCH_SIZE", "100"))


# ---------------------------------------------------------------------------
# Elasticsearch client
# ---------------------------------------------------------------------------

class ElasticsearchIngester:
    """
    Ingest events from Elasticsearch and push to Cerebrum queue.
    """

    def __init__(self):
        self.client: Optional[AsyncElasticsearch] = None
        self.last_timestamp: Optional[datetime] = None
        self.running = True

    async def connect(self) -> None:
        """Establish connection to Elasticsearch."""
        basic_auth = None
        if ELASTICSEARCH_USERNAME and ELASTICSEARCH_PASSWORD:
            basic_auth = (ELASTICSEARCH_USERNAME, ELASTICSEARCH_PASSWORD)

        self.client = AsyncElasticsearch(
            hosts=ELASTICSEARCH_HOSTS,
            basic_auth=basic_auth,
            verify_certs=False,  # For development only
            request_timeout=30,
        )

        # Test connection
        try:
            info = await self.client.info()
            logger.info("Connected to Elasticsearch: %s", info.get("version", {}).get("number", "unknown"))
        except Exception as e:
            logger.error("Failed to connect to Elasticsearch: %s", e)
            raise

    async def get_last_checkpoint(self) -> Optional[datetime]:
        """Load last checkpoint from file or environment."""
        checkpoint_file = "/tmp/elasticsearch_ingest_checkpoint.txt"
        try:
            with open(checkpoint_file, "r") as f:
                ts_str = f.read().strip()
                if ts_str:
                    return datetime.fromisoformat(ts_str)
        except (FileNotFoundError, ValueError):
            pass

        # Default: look back LOOKBACK_HOURS hours
        if LOOKBACK_HOURS > 0:
            return datetime.utcnow() - timedelta(hours=LOOKBACK_HOURS)
        return None

    async def save_checkpoint(self, timestamp: datetime) -> None:
        """Save last processed timestamp."""
        checkpoint_file = "/tmp/elasticsearch_ingest_checkpoint.txt"
        try:
            with open(checkpoint_file, "w") as f:
                f.write(timestamp.isoformat())
            logger.debug("Checkpoint saved: %s", timestamp.isoformat())
        except OSError as e:
            logger.warning("Failed to save checkpoint: %s", e)

    async def query_new_events(self, since: Optional[datetime]) -> List[Dict[str, Any]]:
        """Query Elasticsearch for events newer than `since`."""
        if not self.client:
            return []

        # Build timestamp filter
        timestamp_filter = {"range": {"@timestamp": {"gte": "now-1h"}}}
        if since:
            timestamp_filter = {"range": {"@timestamp": {"gte": since.isoformat()}}}

        query = {
            "query": timestamp_filter,
            "sort": [{"@timestamp": {"order": "asc"}}],
            "size": BATCH_SIZE,
        }

        try:
            response = await self.client.search(
                index=ELASTICSEARCH_INDEX,
                body=query,
            )

            hits = response.get("hits", {}).get("hits", [])
            events = []
            for hit in hits:
                source = hit.get("_source", {})
                # Add Elasticsearch metadata
                source["_elasticsearch_id"] = hit.get("_id")
                source["_elasticsearch_index"] = hit.get("_index")
                events.append(source)

            logger.debug("Query returned %d events", len(events))
            return events

        except NotFoundError:
            logger.warning("Index %s not found", ELASTICSEARCH_INDEX)
            return []
        except Exception as e:
            logger.error("Elasticsearch query failed: %s", e)
            return []

    async def process_event(self, event: Dict[str, Any]) -> bool:
        """
        Normalize and enqueue a single event.
        Returns True if successful.
        """
        normalized = normalize(event, source="elasticsearch")
        if normalized is None:
            logger.warning("Failed to normalize event from Elasticsearch: %r", event.get("type", "unknown"))
            return False

        await enqueue(normalized)
        logger.debug("Enqueued event %s from Elasticsearch", normalized.id)
        return True

    async def run_once(self) -> int:
        """
        Run one ingestion cycle.
        Returns number of events processed.
        """
        since = await self.get_last_checkpoint()
        events = await self.query_new_events(since)

        if not events:
            return 0

        processed = 0
        latest_timestamp = since or datetime.utcnow()

        for event in events:
            success = await self.process_event(event)
            if success:
                processed += 1

            # Update latest timestamp from event
            event_ts = event.get("@timestamp") or event.get("timestamp")
            if event_ts:
                try:
                    if isinstance(event_ts, str):
                        event_ts = datetime.fromisoformat(event_ts.replace("Z", "+00:00"))
                    if event_ts > latest_timestamp:
                        latest_timestamp = event_ts
                except (ValueError, TypeError):
                    pass

        # Save checkpoint
        if processed > 0:
            await self.save_checkpoint(latest_timestamp)

        logger.info("Processed %d events from Elasticsearch", processed)
        return processed

    async def run_forever(self) -> None:
        """Main loop: poll Elasticsearch periodically."""
        await self.connect()

        logger.info("Starting Elasticsearch ingester (poll interval: %ds)", POLL_INTERVAL_SECONDS)

        while self.running:
            try:
                await self.run_once()
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                logger.info("Elasticsearch ingester cancelled")
                break
            except Exception as e:
                logger.exception("Error in Elasticsearch ingester: %s", e)
                await asyncio.sleep(POLL_INTERVAL_SECONDS)

        # Cleanup
        if self.client:
            await self.client.close()

    def stop(self):
        self.running = False


# ---------------------------------------------------------------------------
# Standalone entrypoint
# ---------------------------------------------------------------------------

async def main():
    logging.basicConfig(level=logging.INFO)
    ingester = ElasticsearchIngester()
    await ingester.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
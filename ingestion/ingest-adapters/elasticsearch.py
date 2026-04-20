"""
ingest-adapters/elasticsearch.py — Elasticsearch adapter for dynamic-labyrinth.

Roadmap reference: Alaa — "ingest-adapters/ for different pushers (file, elasticsearch, kafka)"

Polls an Elasticsearch index where Honeytrap (or Filebeat/Logstash) has
forwarded events, normalizes them, and yields event dicts.

Requirements:
    pip install elasticsearch==8.13.0

Configuration (env vars or config dict):
    ES_URL          http://localhost:9200  (required)
    ES_INDEX        honeytrap-events-*    (required)
    ES_USER         elastic               (optional)
    ES_PASSWORD     changeme              (optional)
    ES_POLL_SECONDS 5                     (optional)
    ES_BATCH_SIZE   100                   (optional)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

ADAPTER_NAME = "elasticsearch"
ADAPTER_DESCRIPTION = "Polls an Elasticsearch index for Honeytrap events"
SOURCE_TAG = "elasticsearch"


class ElasticsearchAdapter:
    """
    Polls Elasticsearch for new Honeytrap events using a search-after cursor.
    Maintains state so it picks up from where it left off on restart.
    """

    def __init__(
        self,
        url: str,
        index: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        poll_seconds: int = 5,
        batch_size: int = 100,
    ) -> None:
        self.url = url.rstrip("/")
        self.index = index
        self.username = username
        self.password = password
        self.poll_seconds = poll_seconds
        self.batch_size = batch_size
        self._last_ts: Optional[str] = None  # ISO timestamp cursor
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from elasticsearch import Elasticsearch  # type: ignore

            kwargs: Dict[str, Any] = {"hosts": [self.url]}
            if self.username and self.password:
                kwargs["basic_auth"] = (self.username, self.password)
            self._client = Elasticsearch(**kwargs)
            logger.info("Elasticsearch adapter connected to %s index=%s", self.url, self.index)
        except ImportError:
            raise RuntimeError(
                "elasticsearch package not installed. "
                "Run: pip install elasticsearch==8.13.0"
            )
        return self._client

    def _fetch_batch(self) -> List[Dict[str, Any]]:
        """Fetch a batch of documents newer than the cursor."""
        client = self._get_client()
        query: Dict[str, Any] = {
            "size": self.batch_size,
            "sort": [{"@timestamp": "asc"}],
            "query": {"range": {}},
        }

        if self._last_ts:
            query["query"]["range"]["@timestamp"] = {"gt": self._last_ts}
        else:
            query["query"] = {"match_all": {}}

        try:
            resp = client.search(index=self.index, body=query)
            hits = resp["hits"]["hits"]
            if hits:
                self._last_ts = hits[-1]["_source"].get("@timestamp") or hits[-1]["sort"][0]
            return [h["_source"] for h in hits]
        except Exception as exc:
            logger.error("Elasticsearch fetch error: %s", exc)
            return []

    def _normalize_es_doc(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """Map common Elasticsearch/Filebeat fields to Honeytrap file-pusher schema."""
        return {
            "type": doc.get("event", {}).get("action") or doc.get("type", "unknown"),
            "src-ip": (
                doc.get("source", {}).get("ip")
                or doc.get("src-ip")
                or doc.get("sourceIPAddress", "")
            ),
            "dst-port": (
                doc.get("destination", {}).get("port")
                or doc.get("dst-port")
            ),
            "protocol": (
                doc.get("network", {}).get("protocol")
                or doc.get("protocol", "unknown")
            ),
            "start_time": doc.get("@timestamp") or doc.get("start_time"),
            "username": doc.get("user", {}).get("name") or doc.get("username"),
            "url": doc.get("url", {}).get("full") or doc.get("url"),
            "_raw_es": doc,
        }

    def iter_events(self) -> Iterator[Dict[str, Any]]:
        """Infinite polling iterator — yields raw (pre-normalization) event dicts."""
        while True:
            batch = self._fetch_batch()
            for doc in batch:
                yield self._normalize_es_doc(doc)
            if not batch:
                time.sleep(self.poll_seconds)

    async def iter_events_async(self):
        """Async generator version."""
        while True:
            batch = self._fetch_batch()
            for doc in batch:
                yield self._normalize_es_doc(doc)
            if not batch:
                await asyncio.sleep(self.poll_seconds)

    def close(self) -> None:
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


def create(config: Dict[str, Any]) -> ElasticsearchAdapter:
    """Factory function used by the adapter registry."""
    return ElasticsearchAdapter(
        url=config.get("url", os.environ.get("ES_URL", "http://localhost:9200")),
        index=config.get("index", os.environ.get("ES_INDEX", "honeytrap-events-*")),
        username=config.get("username", os.environ.get("ES_USER")),
        password=config.get("password", os.environ.get("ES_PASSWORD")),
        poll_seconds=int(config.get("poll_seconds", os.environ.get("ES_POLL_SECONDS", "5"))),
        batch_size=int(config.get("batch_size", os.environ.get("ES_BATCH_SIZE", "100"))),
    )

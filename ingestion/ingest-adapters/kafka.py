"""
ingest-adapters/kafka.py — Kafka consumer adapter for dynamic-labyrinth.

Roadmap reference: Alaa — "ingest-adapters/ for different pushers (file, elasticsearch, kafka)"

Consumes events from a Kafka topic where Honeytrap (or a Kafka Connect
source connector) publishes events, then yields normalized event dicts.

Requirements:
    pip install confluent-kafka==2.4.0

Configuration (env vars or config dict):
    KAFKA_BROKERS          localhost:9092          (required)
    KAFKA_TOPIC            honeytrap.events        (required)
    KAFKA_GROUP_ID         dl-ingestion            (optional)
    KAFKA_AUTO_OFFSET      earliest | latest       (optional, default earliest)
    KAFKA_POLL_TIMEOUT_MS  1000                    (optional)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

ADAPTER_NAME = "kafka"
ADAPTER_DESCRIPTION = "Consumes Honeytrap events from a Kafka topic"
SOURCE_TAG = "kafka"


class KafkaAdapter:
    """
    Kafka consumer that reads Honeytrap events and yields raw event dicts.

    Commits offsets after each successful batch to ensure at-least-once delivery.
    """

    def __init__(
        self,
        brokers: str,
        topic: str,
        group_id: str = "dl-ingestion",
        auto_offset_reset: str = "earliest",
        poll_timeout_ms: int = 1000,
    ) -> None:
        self.brokers = brokers
        self.topic = topic
        self.group_id = group_id
        self.auto_offset_reset = auto_offset_reset
        self.poll_timeout_ms = poll_timeout_ms
        self._consumer = None

    def _get_consumer(self):
        if self._consumer is not None:
            return self._consumer
        try:
            from confluent_kafka import Consumer, KafkaError  # type: ignore

            conf = {
                "bootstrap.servers": self.brokers,
                "group.id": self.group_id,
                "auto.offset.reset": self.auto_offset_reset,
                "enable.auto.commit": False,  # manual commit for reliability
                "session.timeout.ms": 10000,
            }
            self._consumer = Consumer(conf)
            self._consumer.subscribe([self.topic])
            logger.info(
                "Kafka adapter subscribed to topic=%s brokers=%s group=%s",
                self.topic, self.brokers, self.group_id,
            )
        except ImportError:
            raise RuntimeError(
                "confluent-kafka package not installed. "
                "Run: pip install confluent-kafka==2.4.0"
            )
        return self._consumer

    def _parse_message(self, msg) -> Optional[Dict[str, Any]]:
        """Parse a Kafka message value as JSON."""
        try:
            value = msg.value()
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            record = json.loads(value)
            # Add Kafka metadata as extra fields for traceability
            record["_kafka_offset"] = msg.offset()
            record["_kafka_partition"] = msg.partition()
            return record
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning(
                "Kafka message parse error (offset=%s): %s",
                msg.offset() if msg else "?", exc,
            )
            return None

    def iter_events(self) -> Iterator[Dict[str, Any]]:
        """
        Infinite iterator that polls Kafka and yields event dicts.
        Commits offset after every successful yield.
        """
        from confluent_kafka import KafkaError  # type: ignore

        consumer = self._get_consumer()
        timeout_s = self.poll_timeout_ms / 1000.0

        while True:
            msg = consumer.poll(timeout=timeout_s)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("Kafka consumer error: %s", msg.error())
                time.sleep(1)
                continue

            record = self._parse_message(msg)
            if record is not None:
                yield record
                consumer.commit(message=msg)  # at-least-once

    async def iter_events_async(self):
        """Async generator — polls Kafka in executor to avoid blocking event loop."""
        loop = asyncio.get_event_loop()

        from confluent_kafka import KafkaError  # type: ignore
        consumer = self._get_consumer()
        timeout_s = self.poll_timeout_ms / 1000.0

        while True:
            msg = await loop.run_in_executor(None, consumer.poll, timeout_s)
            if msg is None:
                await asyncio.sleep(0.01)
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("Kafka error: %s", msg.error())
                await asyncio.sleep(1)
                continue

            record = self._parse_message(msg)
            if record is not None:
                yield record
                await loop.run_in_executor(None, consumer.commit, None, False, msg)

    def close(self) -> None:
        if self._consumer:
            try:
                self._consumer.close()
            except Exception:
                pass
            self._consumer = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def create(config: Dict[str, Any]) -> KafkaAdapter:
    """Factory function used by the adapter registry."""
    return KafkaAdapter(
        brokers=config.get("brokers", os.environ.get("KAFKA_BROKERS", "localhost:9092")),
        topic=config.get("topic", os.environ.get("KAFKA_TOPIC", "honeytrap.events")),
        group_id=config.get("group_id", os.environ.get("KAFKA_GROUP_ID", "dl-ingestion")),
        auto_offset_reset=config.get("auto_offset_reset", os.environ.get("KAFKA_AUTO_OFFSET", "earliest")),
        poll_timeout_ms=int(config.get("poll_timeout_ms", os.environ.get("KAFKA_POLL_TIMEOUT_MS", "1000"))),
    )

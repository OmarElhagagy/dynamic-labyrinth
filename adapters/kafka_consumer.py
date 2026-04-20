# ingestion/adapters/kafka_consumer.py
"""
Kafka adapter for ingestion service.

Consumes events from Kafka topics and normalizes them
into the canonical schema for Cerebrum.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from typing import Any, Dict, Optional

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError

# Add parent directory to path
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalize import normalize
from queue_manager import enqueue

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092").split(",")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "honeytrap-events")
KAFKA_GROUP_ID = os.environ.get("KAFKA_GROUP_ID", "ingestion-service")
KAFKA_AUTO_OFFSET_RESET = os.environ.get("KAFKA_AUTO_OFFSET_RESET", "earliest")
KAFKA_SECURITY_PROTOCOL = os.environ.get("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")
KAFKA_SASL_MECHANISM = os.environ.get("KAFKA_SASL_MECHANISM", "")
KAFKA_SASL_USERNAME = os.environ.get("KAFKA_SASL_USERNAME", "")
KAFKA_SASL_PASSWORD = os.environ.get("KAFKA_SASL_PASSWORD", "")


# ---------------------------------------------------------------------------
# Kafka Consumer
# ---------------------------------------------------------------------------

class KafkaIngester:
    """
    Consume events from Kafka and push to Cerebrum queue.
    """

    def __init__(self):
        self.consumer: Optional[AIOKafkaConsumer] = None
        self.running = True
        self.stats = {
            "messages_received": 0,
            "events_normalized": 0,
            "events_failed": 0,
        }

    def _build_consumer(self) -> AIOKafkaConsumer:
        """Build Kafka consumer with optional SASL authentication."""
        consumer_kwargs = {
            "bootstrap_servers": KAFKA_BOOTSTRAP_SERVERS,
            "group_id": KAFKA_GROUP_ID,
            "auto_offset_reset": KAFKA_AUTO_OFFSET_RESET,
            "enable_auto_commit": True,
            "auto_commit_interval_ms": 5000,
            "value_deserializer": lambda m: json.loads(m.decode("utf-8")) if m else None,
        }

        # SASL authentication (if configured)
        if KAFKA_SECURITY_PROTOCOL != "PLAINTEXT":
            consumer_kwargs["security_protocol"] = KAFKA_SECURITY_PROTOCOL
            if KAFKA_SASL_MECHANISM:
                consumer_kwargs["sasl_mechanism"] = KAFKA_SASL_MECHANISM
                consumer_kwargs["sasl_plain_username"] = KAFKA_SASL_USERNAME
                consumer_kwargs["sasl_plain_password"] = KAFKA_SASL_PASSWORD

        return AIOKafkaConsumer(
            KAFKA_TOPIC,
            **consumer_kwargs,
        )

    async def start(self) -> None:
        """Start the Kafka consumer."""
        self.consumer = self._build_consumer()
        await self.consumer.start()
        logger.info("Kafka consumer started: topic=%s, servers=%s", KAFKA_TOPIC, KAFKA_BOOTSTRAP_SERVERS)

    async def process_message(self, message: Any) -> bool:
        """
        Process a single Kafka message.
        Returns True if event was normalized and enqueued.
        """
        self.stats["messages_received"] += 1

        if message.value is None:
            logger.warning("Empty message received")
            return False

        event_data = message.value

        # Extract source hint from headers if available
        source = "kafka"
        if message.headers:
            for key, value in message.headers:
                if key == "source" and value:
                    source = value.decode("utf-8")

        normalized = normalize(event_data, source=source)
        if normalized is None:
            self.stats["events_failed"] += 1
            logger.warning("Failed to normalize Kafka event: %r", event_data.get("type", "unknown"))
            return False

        await enqueue(normalized)
        self.stats["events_normalized"] += 1

        if self.stats["messages_received"] % 100 == 0:
            logger.info(
                "Kafka stats: received=%d, normalized=%d, failed=%d",
                self.stats["messages_received"],
                self.stats["events_normalized"],
                self.stats["events_failed"],
            )

        return True

    async def run_forever(self) -> None:
        """Main loop: consume messages from Kafka."""
        await self.start()

        try:
            async for message in self.consumer:
                if not self.running:
                    break
                await self.process_message(message)
        except asyncio.CancelledError:
            logger.info("Kafka consumer cancelled")
        except Exception as e:
            logger.exception("Kafka consumer error: %s", e)
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the Kafka consumer."""
        self.running = False
        if self.consumer:
            await self.consumer.stop()
            logger.info("Kafka consumer stopped")

    def get_stats(self) -> Dict[str, int]:
        """Return current statistics."""
        return dict(self.stats)


# ---------------------------------------------------------------------------
# Standalone entrypoint
# ---------------------------------------------------------------------------

async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    ingester = KafkaIngester()

    # Handle shutdown signals
    loop = asyncio.get_running_loop()

    def shutdown():
        logger.info("Shutdown signal received")
        ingester.running = False

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown)

    await ingester.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
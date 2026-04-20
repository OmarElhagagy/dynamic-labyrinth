# Ingestion Service

The **ingestion** service is responsible for collecting raw events from Honeytrap
instances, normalizing them into the canonical schema, and delivering them to
**Cerebrum** for decision-making.

---

## Architecture

```
Honeytrap (file pusher)  →  file_ingest.py  (tail JSONL files)
                                    ↓
Honeytrap (HTTP pusher)  →  POST /ingest/honeytrap
                                    ↓
Any internal service     →  POST /ingest/event   (HMAC-required)
                                    ↓
                            normalize.py   (Pydantic validation + field mapping)
                                    ↓
                            queue_manager.py  (Redis or in-memory asyncio.Queue)
                                    ↓
                            → Cerebrum POST /events  (HMAC-signed, with retry)
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `CEREBRUM_URL` | `http://cerebrum:8001` | Cerebrum service base URL |
| `HMAC_SECRET` | `change-me-in-production` | Shared secret for HMAC auth |
| `HMAC_REPLAY_WINDOW` | `30` | Max age (seconds) for HMAC timestamps |
| `REDIS_URL` | _(empty)_ | Redis URL, e.g. `redis://redis:6379/0`. If empty, falls back to in-memory queue |
| `INGEST_LOG_PATHS` | `/var/log/honeytrap/events.jsonl` | Comma-separated JSONL file paths to tail |
| `INGEST_FROM_START` | `false` | Replay entire file on startup |
| `INGEST_POLL_MS` | `200` | File polling interval (ms) |
| `ENABLE_FILE_INGEST` | `true` | Enable JSONL file tailing |
| `HONEYTRAP_ALLOWED_IPS` | _(empty)_ | Comma-separated IPs allowed to POST to `/ingest/honeytrap`. Empty = all IPs allowed |
| `REQUIRE_HMAC_ON_WEBHOOK` | `false` | Enforce HMAC on `/ingest/honeytrap` |
| `LOG_LEVEL` | `INFO` | Logging level |
| `DLQ_PATH` | `/tmp/dl_dead_letter.jsonl` | Dead-letter file for permanently failed deliveries |

---

## Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Start the service
CEREBRUM_URL=http://localhost:8001 \
HMAC_SECRET=my-secret \
uvicorn main:app --host 0.0.0.0 --port 8002 --reload
```

---

## API Endpoints

### `POST /ingest/event` — Single event (HMAC required)

```bash
BODY='{"type":"authentication_failed","src-ip":"1.2.3.4","protocol":"ssh","username":"root","password":"pass"}'
TS=$(date +%s)
SIG=$(echo -e "POST\n/ingest/event\n${TS}\n$(echo -n "${BODY}" | sha256sum | cut -d' ' -f1)" \
      | openssl dgst -sha256 -hmac "my-secret" | awk '{print $2}')

curl -X POST http://localhost:8002/ingest/event \
  -H "Content-Type: application/json" \
  -H "X-DL-Timestamp: ${TS}" \
  -H "X-DL-Signature: ${SIG}" \
  -d "{\"event\": ${BODY}, \"source\": \"file\"}"
```

### `POST /ingest/bulk` — Bulk events (HMAC required)

```bash
curl -X POST http://localhost:8002/ingest/bulk \
  -H "Content-Type: application/json" \
  -H "X-DL-Timestamp: ..." \
  -H "X-DL-Signature: ..." \
  -d '{"events": [{...}, {...}], "source": "file"}'
```

### `POST /ingest/honeytrap` — Honeytrap webhook (no auth by default)

```bash
curl -X POST http://localhost:8002/ingest/honeytrap \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "authentication_failed",
    "source_ip": "10.0.0.5",
    "destination_port": 22,
    "protocol": "ssh",
    "timestamp": "2025-10-16T19:00:00Z",
    "data": {"username": "admin", "password": "admin123"}
  }'
```

### `POST /ingest/replay` — Replay a JSONL file (HMAC required)

```bash
curl -X POST http://localhost:8002/ingest/replay \
  -H "X-DL-Timestamp: ..." \
  -H "X-DL-Signature: ..." \
  -d '{"path": "/var/log/honeytrap/archive.jsonl", "source": "file", "limit": 5000}'
```

### `GET /health` — Health check

```json
{
  "status": "ok",
  "queue_size": 42,
  "redis_connected": true,
  "cerebrum_reachable": true
}
```

### `GET /metrics` — Prometheus metrics

### `GET /stats` — JSON statistics

---

## Canonical Event Schema

```json
{
  "id": "evt-a3f2c1b4d5e6f7a8",
  "session_id": "src_a3f2c1b4",
  "timestamp": "2025-10-16T19:00:00+00:00",
  "protocol": "ssh",
  "event_type": "authentication_failed",
  "indicators": ["user:root", "password_attempt:pa***"],
  "source_ip": "1.2.3.4",
  "destination_port": 22,
  "raw": { "...original record..." },
  "ingestion_source": "file"
}
```

---

## Running Tests

```bash
cd ingestion
pip install -r requirements.txt pytest pytest-asyncio
pytest tests/ -v
```

---

## Supported Pusher Formats

| Source | Description |
|---|---|
| `file` | Honeytrap JSONL file-pusher format (`src-ip`, `type`, `start_time`, …) |
| `http` / `webhook` | Honeytrap HTTP-pusher webhook format (`source_ip`, `event_type`, `data`, …) |
| `generic` | Auto-detection fallback using common field name heuristics |

To add a new adapter, register a function in `normalize.ADAPTER_MAP`.

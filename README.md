# 🌀 Dynamic Labyrinth

> **Adaptive Honeypot Orchestration System** — A multi-tiered deception platform that dynamically escalates attackers through increasingly sophisticated honeypot environments based on real-time threat analysis.

[![CI](https://github.com/YOUR_ORG/dynamic-labyrinth/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_ORG/dynamic-labyrinth/actions/workflows/ci.yml)
[![Security](https://github.com/YOUR_ORG/dynamic-labyrinth/actions/workflows/security.yml/badge.svg)](https://github.com/YOUR_ORG/dynamic-labyrinth/actions/workflows/security.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## 📖 Table of Contents

- [Overview](#-overview)
- [Architecture](#-architecture)
- [Components](#-components)
- [Quick Start](#-quick-start)
- [Configuration](#-configuration)
- [API Reference](#-api-reference)
- [Operations](#-operations)
- [Development](#-development)
- [Team](#-team)

---

## 🎯 Overview

Dynamic Labyrinth is a honeypot orchestration system that:

1. **Detects** malicious connections via lightweight Level 1 honeypots
2. **Analyzes** attacker behavior using ML-based threat scoring (Cerebrum)
3. **Escalates** sophisticated attackers to higher-fidelity environments
4. **Routes** sessions seamlessly via nginx cookie-based session affinity
5. **Captures** rich telemetry for threat intelligence

### Escalation Tiers

| Level | Fidelity | Purpose | Pool Size |
|-------|----------|---------|-----------|
| **L1** | Low | Initial detection, quick fingerprinting | 5 containers |
| **L2** | Medium | Extended interaction, behavioral analysis | 3 containers |
| **L3** | High | Full system emulation, APT engagement | 1 container |

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              INTERNET                                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           NGINX REVERSE PROXY                                │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐              │
│  │  HTTP Routing   │  │  Stream Proxy   │  │  Cookie Map     │              │
│  │  (80, 443)      │  │  (22,21,23...)  │  │  (dlsess→pool)  │              │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘              │
└─────────────────────────────────────────────────────────────────────────────┘
            │                       │                       │
            ▼                       ▼                       ▼
┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐
│   LEVEL 1 POOL    │  │   LEVEL 2 POOL    │  │   LEVEL 3 POOL    │
│  ┌─────┐ ┌─────┐  │  │  ┌─────┐ ┌─────┐  │  │     ┌─────┐       │
│  │ L1a │ │ L1b │  │  │  │ L2a │ │ L2b │  │  │     │ L3  │       │
│  └─────┘ └─────┘  │  │  └─────┘ └─────┘  │  │     └─────┘       │
│  ┌─────┐ ┌─────┐  │  │     ┌─────┐       │  │                   │
│  │ L1c │ │ L1d │  │  │     │ L2c │       │  │                   │
│  └─────┘ └─────┘  │  │     └─────┘       │  │                   │
│     ┌─────┐       │  │                   │  │                   │
│     │ L1e │       │  │                   │  │                   │
│     └─────┘       │  │                   │  │                   │
└───────────────────┘  └───────────────────┘  └───────────────────┘
            │                       │                       │
            └───────────────────────┼───────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            ORCHESTRATOR (FastAPI)                            │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐              │
│  │  Pool Manager   │  │  Nginx Writer   │  │  Session Store  │              │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘              │
└─────────────────────────────────────────────────────────────────────────────┘
            │                       │                       │
            ▼                       ▼                       ▼
┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐
│     CEREBRUM      │  │     DISCOVERY     │  │     INGESTION     │
│  (ML Analysis)    │  │  (Service Enum)   │  │  (Event Pipeline) │
└───────────────────┘  └───────────────────┘  └───────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              DASHBOARD                                       │
│                    (Real-time Visualization)                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Network Layout

| Network | CIDR | Purpose |
|---------|------|---------|
| `frontend` | 10.0.1.0/24 | Public-facing (nginx, honeypots) |
| `backend` | 10.0.2.0/24 | Internal services (orchestrator, cerebrum) |
| `management` | 10.0.3.0/24 | Operations (monitoring, backups) |

---

---

## 🚀 Quick Start

### Prerequisites

- Docker 24.0+
- Docker Compose 2.20+
- Python 3.11+ (for development)
- Go 1.21+ (for honeytrap development)

### 1. Clone Repository

```bash
git clone https://github.com/YOUR_ORG/dynamic-labyrinth.git
cd dynamic-labyrinth
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings
```

### 3. Start Services

```bash
# Development mode (with hot reload)
docker-compose up -d

# Production mode (with resource limits)
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### 4. Pre-warm Container Pools

```bash
./infra/scripts/prewarm.sh
```

### 5. Verify Deployment

```bash
# Check health
curl http://localhost:8080/healthz

# Check pool status
python infra/scripts/pool_status.py

# View logs
docker-compose logs -f orchestrator
```

---

## ⚙️ Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ORCHESTRATOR_HOST` | `0.0.0.0` | Bind address |
| `ORCHESTRATOR_PORT` | `8080` | HTTP port |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/orchestrator.db` | Database connection |
| `SECRET_KEY` | (required) | HMAC signing key |
| `CEREBRUM_URL` | `http://cerebrum:8000` | ML scoring endpoint |
| `NGINX_MAP_PATH` | `/etc/nginx/maps/session_map.conf` | Session routing map |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### Pool Configuration (`orchestrator/pools.yaml`)

```yaml
pools:
  level1:
    image: ghcr.io/your-org/honeytrap-level1:latest
    min_size: 5
    max_size: 10
    network: frontend
    
  level2:
    image: ghcr.io/your-org/honeytrap-level2:latest
    min_size: 3
    max_size: 6
    network: frontend
    
  level3:
    image: ghcr.io/your-org/honeytrap-level3:latest
    min_size: 1
    max_size: 2
    network: frontend
```

---

## 📡 API Reference

### Endpoints

#### `POST /escalate`

Request escalation decision from Cerebrum and route session.

```bash
curl -X POST http://localhost:8080/escalate \
  -H "Content-Type: application/json" \
  -H "X-Signature: <hmac-signature>" \
  -H "X-Timestamp: <unix-timestamp>" \
  -d '{
    "session_id": "abc123",
    "source_ip": "192.168.1.100",
    "current_level": 1,
    "threat_score": 0.75,
    "indicators": ["port_scan", "brute_force"]
  }'
```

**Response:**
```json
{
  "session_id": "abc123",
  "decision": "escalate",
  "target_level": 2,
  "container_id": "honeytrap-l2-abc123",
  "reason": "High threat score with multiple indicators"
}
```

#### `GET /session/{session_id}`

Get session details.

```bash
curl http://localhost:8080/session/abc123 \
  -H "X-Signature: <hmac-signature>" \
  -H "X-Timestamp: <unix-timestamp>"
```

#### `GET /pools`

Get pool status and statistics.

```bash
curl http://localhost:8080/pools \
  -H "X-Signature: <hmac-signature>" \
  -H "X-Timestamp: <unix-timestamp>"
```

**Response:**
```json
{
  "level1": {
    "available": 3,
    "in_use": 2,
    "total": 5,
    "healthy": 5
  },
  "level2": {
    "available": 2,
    "in_use": 1,
    "total": 3,
    "healthy": 3
  },
  "level3": {
    "available": 1,
    "in_use": 0,
    "total": 1,
    "healthy": 1
  }
}
```

#### `GET /healthz`

Health check endpoint.

#### `GET /metrics`

Prometheus metrics endpoint.

### Authentication

All endpoints (except `/healthz`) require HMAC-SHA256 authentication:

```python
import hmac
import hashlib
import time

def sign_request(secret_key: str, body: str = "") -> tuple[str, str]:
    timestamp = str(int(time.time()))
    message = f"{timestamp}:{body}"
    signature = hmac.new(
        secret_key.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    return signature, timestamp
```

---

## 🔧 Operations

### Health Checks

```bash
# Check all containers
./infra/scripts/healthcheck.sh

# Check specific pool
./infra/scripts/healthcheck.sh level1
```

### Deployment

```bash
# Deploy with zero downtime
./infra/scripts/deploy.sh

# Rollback to previous version
./infra/scripts/rollback.sh
```

### Backup & Restore

```bash
# Create backup
./infra/scripts/backup.sh

# Backups stored in: ./backups/YYYY-MM-DD_HH-MM-SS/
```

### Monitoring

```bash
# Start monitoring stack (Prometheus + Grafana)
./infra/scripts/monitoring.sh start

# Access Grafana: http://localhost:3000
# Default credentials: admin/admin
```

### Pool Management

```bash
# View pool status
python infra/scripts/pool_status.py

# Output:
# ┌─────────┬───────────┬────────┬───────┬─────────┐
# │ Level   │ Available │ In Use │ Total │ Healthy │
# ├─────────┼───────────┼────────┼───────┼─────────┤
# │ level1  │ 3         │ 2      │ 5     │ 5       │
# │ level2  │ 2         │ 1      │ 3     │ 3       │
# │ level3  │ 1         │ 0      │ 1     │ 1       │
# └─────────┴───────────┴────────┴───────┴─────────┘
```

---

## 🛠 Development

### Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
.\venv\Scripts\activate   # Windows

# Install dependencies
pip install -r orchestrator/requirements.txt
pip install -r tests/requirements.txt

# Install pre-commit hooks
pre-commit install
```

### Running Tests

```bash
# Unit tests
cd orchestrator
pytest --cov=. --cov-report=html

# Integration tests
docker-compose up -d
pytest tests/integration/

# Load tests
./tests/load/run_load_test.sh
```

### Code Quality

```bash
# Format code
black orchestrator/
isort orchestrator/

# Lint
ruff check orchestrator/

# Type check
mypy orchestrator/
```

### Building Images

```bash
# Build all images locally
docker-compose build

# Build specific image
docker build -f docker/honeytrap-level1.Dockerfile -t honeytrap-level1 .
```

---

]

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🔗 Related Documentation

- [API Specification](docs/api/openapi.yaml)
- [Operations Runbook](docs/runbook.md)
- [Architecture Decision Records](docs/adr/)
- [Integration Guide](docs/integration/)

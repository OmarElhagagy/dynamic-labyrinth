FROM python:3.11-slim

LABEL maintainer="dynamic-labyrinth team"
LABEL service="ingestion"

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy service source
COPY . .

# Create log directory (for file ingest; can be overridden by volume mount)
RUN mkdir -p /var/log/honeytrap /tmp

# Non-root user
RUN useradd -r -s /bin/false ingestion
RUN chown -R ingestion:ingestion /app /var/log/honeytrap
USER ingestion

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8002/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", "--log-level", "info", "--access-log"]

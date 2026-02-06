# =============================================================================
# Dynamic Labyrinth - Honeytrap Level 3 Dockerfile
# =============================================================================
# High-interaction honeypot with full service emulation.
# Services: SSH, HTTP, Telnet, FTP, SMTP, DNS, VNC, Redis, Elasticsearch, Docker
# Use case: Sophisticated attackers requiring detailed analysis
# =============================================================================

FROM golang:1.21-alpine AS builder

# Build arguments
ARG LDFLAGS=""

# Install build dependencies
RUN apk add --no-cache git make libpcap-dev gcc musl-dev

# Set working directory
WORKDIR /src/honeytrap

# Copy honeytrap source
COPY honeytrap/ .

# Build honeytrap binary with all features
RUN CGO_ENABLED=1 GOOS=linux go build \
    -a \
    -tags="pcap" \
    -ldflags="${LDFLAGS}" \
    -o /go/bin/honeytrap .

# =============================================================================
# Runtime Stage
# =============================================================================
FROM alpine:3.19

LABEL maintainer="Dynamic Labyrinth Team"
LABEL description="Honeytrap Level 3 - High Interaction Honeypot"
LABEL level="3"
LABEL interaction="high"

# Install runtime dependencies including pcap for packet capture
RUN apk --no-cache add \
    ca-certificates \
    tzdata \
    busybox-extras \
    libpcap \
    tcpdump \
    && update-ca-certificates

# Create directories
RUN mkdir -p /config /data /sessions /pcap /snapshots

# Copy binary from builder
COPY --from=builder /go/bin/honeytrap /honeytrap/honeytrap

# Copy level 3 configuration
COPY docker/configs/level3.toml /config/config.toml

# Create non-root user with additional capabilities
RUN adduser -D -H -s /sbin/nologin honeytrap && \
    chown -R honeytrap:honeytrap /config /data /sessions /pcap /snapshots /honeytrap

# Note: Running as root for pcap capabilities in high-interaction mode
# In production, use capabilities instead: setcap cap_net_raw+ep /honeytrap/honeytrap
USER honeytrap

# Environment variables
ENV HONEYTRAP_LEVEL=3 \
    HONEYTRAP_INTERACTION=high \
    HONEYTRAP_CONFIG=/config/config.toml \
    HONEYTRAP_DATA=/data \
    HONEYTRAP_RECORD_SESSIONS=true \
    HONEYTRAP_RECORD_PCAP=true \
    HONEYTRAP_FILESYSTEM_SNAPSHOT=true

# Expose all service ports
# SSH: 22, HTTP: 80/443, Telnet: 23, FTP: 21, SMTP: 25, DNS: 53
# VNC: 5900, Redis: 6379, Elasticsearch: 9200, Docker: 2375
EXPOSE 21 22 23 25 53 80 443 2375 5900 6379 9200

# Health check - verify honeytrap process is running
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD pidof honeytrap > /dev/null || exit 1

# Volumes for persistent data
VOLUME ["/data", "/sessions", "/pcap", "/snapshots"]

# Entry point
ENTRYPOINT ["/honeytrap/honeytrap"]
CMD ["--config", "/config/config.toml", "--data", "/data/"]

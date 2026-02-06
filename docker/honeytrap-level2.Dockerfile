# =============================================================================
# Dynamic Labyrinth - Honeytrap Level 2 Dockerfile
# =============================================================================
# Medium-interaction honeypot with enhanced service emulation.
# Services: SSH, HTTP, Telnet, FTP, SMTP, DNS
# Use case: Attackers showing skill and persistence
# =============================================================================

FROM golang:1.21-alpine AS builder

# Build arguments
ARG LDFLAGS=""

# Install build dependencies
RUN apk add --no-cache git make

# Set working directory
WORKDIR /src/honeytrap

# Copy honeytrap source
COPY honeytrap/ .

# Build honeytrap binary with additional tags
RUN CGO_ENABLED=0 GOOS=linux go build \
    -a -installsuffix cgo \
    -tags="" \
    -ldflags="${LDFLAGS}" \
    -o /go/bin/honeytrap .

# =============================================================================
# Runtime Stage
# =============================================================================
FROM alpine:3.19

LABEL maintainer="Dynamic Labyrinth Team"
LABEL description="Honeytrap Level 2 - Medium Interaction Honeypot"
LABEL level="2"
LABEL interaction="medium"

# Install runtime dependencies
RUN apk --no-cache add \
    ca-certificates \
    tzdata \
    busybox-extras \
    && update-ca-certificates

# Create directories
RUN mkdir -p /config /data /sessions

# Copy binary from builder
COPY --from=builder /go/bin/honeytrap /honeytrap/honeytrap

# Copy level 2 configuration
COPY docker/configs/level2.toml /config/config.toml

# Create non-root user
RUN adduser -D -H -s /sbin/nologin honeytrap && \
    chown -R honeytrap:honeytrap /config /data /sessions /honeytrap

USER honeytrap

# Environment variables
ENV HONEYTRAP_LEVEL=2 \
    HONEYTRAP_INTERACTION=medium \
    HONEYTRAP_CONFIG=/config/config.toml \
    HONEYTRAP_DATA=/data \
    HONEYTRAP_RECORD_SESSIONS=true

# Expose service ports
# SSH: 22, HTTP: 80, Telnet: 23, FTP: 21, SMTP: 25, DNS: 53
EXPOSE 21 22 23 25 53 80

# Health check - verify honeytrap process is running (PID 1 in container)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD test -f /proc/1/cmdline || exit 1

# Volumes for persistent data
VOLUME ["/data", "/sessions"]

# Entry point
ENTRYPOINT ["/honeytrap/honeytrap"]
CMD ["--config", "/config/config.toml", "--data", "/data/"]

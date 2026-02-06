# =============================================================================
# Dynamic Labyrinth - Honeytrap Level 1 Dockerfile
# =============================================================================
# Low-interaction honeypot with basic service emulation.
# Services: SSH, HTTP, Telnet
# Use case: Initial attacker engagement and reconnaissance detection
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

# Build honeytrap binary
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
LABEL description="Honeytrap Level 1 - Low Interaction Honeypot"
LABEL level="1"
LABEL interaction="low"

# Install runtime dependencies (busybox-extras provides nc for healthcheck)
RUN apk --no-cache add \
    ca-certificates \
    tzdata \
    busybox-extras \
    && update-ca-certificates

# Create directories
RUN mkdir -p /config /data

# Copy binary from builder
COPY --from=builder /go/bin/honeytrap /honeytrap/honeytrap

# Copy level 1 configuration
COPY docker/configs/level1.toml /config/config.toml

# Create non-root user
RUN adduser -D -H -s /sbin/nologin honeytrap && \
    chown -R honeytrap:honeytrap /config /data /honeytrap

USER honeytrap

# Environment variables
ENV HONEYTRAP_LEVEL=1 \
    HONEYTRAP_INTERACTION=low \
    HONEYTRAP_CONFIG=/config/config.toml \
    HONEYTRAP_DATA=/data

# Expose service ports
# SSH: 22, HTTP: 80, Telnet: 23
EXPOSE 22 80 23

# Health check - verify honeytrap is listening on SSH port (production-ready)
# Uses TCP check instead of process check for reliability
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=5 \
    CMD nc -z 127.0.0.1 22 || exit 1

# Volume for persistent data
VOLUME ["/data"]

# Entry point
ENTRYPOINT ["/honeytrap/honeytrap"]
CMD ["--config", "/config/config.toml", "--data", "/data/"]

# =============================================================================
# Dynamic Labyrinth - Nginx Dockerfile
# =============================================================================
# Custom nginx image with cookie-based routing support
# =============================================================================

FROM nginx:1.25-alpine

LABEL maintainer="Dynamic Labyrinth Team"
LABEL description="Nginx reverse proxy with cookie-based routing"

# Install additional tools
RUN apk --no-cache add \
    curl \
    bash

# Create directories
RUN mkdir -p /etc/nginx/maps /etc/nginx/conf.d

# Copy nginx configuration
COPY docker/nginx/nginx.conf /etc/nginx/nginx.conf
COPY docker/nginx/conf.d/ /etc/nginx/conf.d/

# Create empty map file (will be populated by orchestrator)
RUN touch /etc/nginx/maps/honeytrap_upstream.map && \
    echo 'map $cookie_dlsess $honeytrap_upstream { default "level1_pool"; }' > /etc/nginx/maps/honeytrap_upstream.map

# Set permissions
RUN chown -R nginx:nginx /etc/nginx/maps

# Expose ports
EXPOSE 80 443

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -sf http://localhost/health || exit 1

# Run nginx
CMD ["nginx", "-g", "daemon off;"]

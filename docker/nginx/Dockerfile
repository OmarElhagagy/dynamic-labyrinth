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

# Copy entrypoint script
COPY docker/nginx/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Set permissions for maps directory (will be populated at runtime)
RUN mkdir -p /etc/nginx/maps && chown -R nginx:nginx /etc/nginx/maps

# Expose ports
EXPOSE 80 443

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -sf http://localhost/health || exit 1

# Use entrypoint to ensure map file exists before nginx starts
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]

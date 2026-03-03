# Pin to a specific digest in production for reproducible builds:
#   docker pull python:3.11-slim
#   docker inspect --format='{{index .RepoDigests 0}}' python:3.11-slim
# Then use: FROM python:3.11-slim@sha256:<digest>
FROM python:3.11-slim@sha256:bb01a2ca1e0b0bc680a1e2a8ee1de57eaf258e8fb4a16f2baa63ae04f6a2e0d8

WORKDIR /app

# Install su-exec for dropping privileges in entrypoint
RUN apt-get update && \
    apt-get install -y --no-install-recommends gosu && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ ./src/
COPY config/ ./config/

# Create non-root user with Docker socket access
# DOCKER_GID should match host's docker.sock group: ls -ln /var/run/docker.sock
# Default 281 works for Unraid; override via .env or docker-compose build args
ARG DOCKER_GID=281
RUN useradd -m appuser && \
    chown -R appuser:appuser /app && \
    if [ "$DOCKER_GID" != "0" ]; then \
        groupadd -g ${DOCKER_GID} dockersock 2>/dev/null || true; \
        usermod -aG ${DOCKER_GID} appuser; \
    fi

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Container starts as root; entrypoint drops to PUID:PGID after fixing permissions
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "src.main"]

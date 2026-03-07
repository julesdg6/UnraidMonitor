# Pin to a specific digest in production for reproducible builds:
#   docker pull python:3.11-slim
#   docker inspect --format='{{index .RepoDigests 0}}' python:3.11-slim
# Then use: FROM python:3.11-slim@sha256:<digest>
FROM python:3.11-slim@sha256:d6e4d224f70f9e0172a06a3a2eba2f768eb146811a349278b38fff3a36463b47

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

# Pin to a specific digest in production for reproducible builds:
#   docker pull python:3.11-slim
#   docker inspect --format='{{index .RepoDigests 0}}' python:3.11-slim
# Then use: FROM python:3.11-slim@sha256:<digest>
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ ./src/
COPY config/ ./config/

# Create non-root user with Docker socket access
# DOCKER_GID should match host's docker.sock group: ls -ln /var/run/docker.sock
# Default 281 works for Unraid; override via .env or docker-compose build args
# If socket access fails, uncomment "user: root" in docker-compose.yml as fallback
ARG DOCKER_GID=281
RUN useradd -m appuser && \
    chown -R appuser:appuser /app && \
    if [ "$DOCKER_GID" != "0" ]; then \
        groupadd -g ${DOCKER_GID} dockersock 2>/dev/null || true; \
        usermod -aG ${DOCKER_GID} appuser; \
    fi

# Default to appuser, but docker-compose can override with "user: root"
USER appuser

CMD ["python", "-m", "src.main"]

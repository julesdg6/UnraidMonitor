FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY src/ ./src/
COPY config/ ./config/

# Create non-root user
# For Docker socket access, either:
#   1. Set DOCKER_GID to match host's docker.sock group (ls -la /var/run/docker.sock)
#   2. Or use "user: root" in docker-compose.yml
ARG DOCKER_GID=0
RUN useradd -m appuser && \
    chown -R appuser:appuser /app && \
    if [ "$DOCKER_GID" != "0" ]; then \
        groupadd -g ${DOCKER_GID} dockersock 2>/dev/null || true; \
        usermod -aG ${DOCKER_GID} appuser; \
    fi

# Default to appuser, but docker-compose can override with "user: root"
USER appuser

CMD ["python", "-m", "src.main"]

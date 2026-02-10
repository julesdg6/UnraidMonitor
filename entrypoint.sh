#!/bin/sh
set -e

# Default to nobody:users (Unraid standard) if PUID/PGID not set
PUID="${PUID:-99}"
PGID="${PGID:-100}"

# Ensure the app directories exist and have correct ownership
# This fixes the root-owned dirs created by Docker on first run
for dir in /app/config /app/data; do
    mkdir -p "$dir"
    chown "$PUID:$PGID" "$dir"
    chmod 755 "$dir"
done

# Also fix ownership of any existing files in data/config dirs
chown -R "$PUID:$PGID" /app/data 2>/dev/null || true
chown -R "$PUID:$PGID" /app/config 2>/dev/null || true

# Update appuser UID/GID to match PUID/PGID
usermod -o -u "$PUID" appuser 2>/dev/null || true
groupmod -o -g "$PGID" appuser 2>/dev/null || true

# Set permissive umask so created files (config.yaml, mute/ignore JSON)
# are readable/writable by group and others (ownership handles access control)
umask 0000

# Drop privileges and run as appuser
exec gosu appuser "$@"

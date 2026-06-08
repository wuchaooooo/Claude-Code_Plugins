#!/usr/bin/env bash
# Start whisper-cpp server if not already running.
#
# Default: model /opt/homebrew/share/whisper-cpp/models/ggml-large-v3-turbo.bin,
# host 0.0.0.0, port 8080. Override via env vars.
#
# Exits 0 if the server is up (already running or started by this script).
# Exits non-zero only on a hard failure (binary missing, model missing,
# health check never succeeded within WAIT_TIMEOUT).
#
# Usage:
#   ./start_whisper_server.sh           # start with defaults
#   WHISPER_PORT=9000 ./start_whisper_server.sh

set -euo pipefail

WHISPER_HOST="${WHISPER_HOST:-0.0.0.0}"
WHISPER_PORT="${WHISPER_PORT:-8080}"
WHISPER_MODEL="${WHISPER_MODEL:-/opt/homebrew/share/whisper-cpp/models/ggml-large-v3-turbo.bin}"
WHISPER_BIN="${WHISPER_BIN:-whisper-server}"
PID_FILE="${WHISPER_PID_FILE:-/tmp/whisper-server.pid}"
LOG_FILE="${WHISPER_LOG_FILE:-/tmp/whisper-server.log}"
HEALTH_TIMEOUT="${WHISPER_HEALTH_TIMEOUT:-30}"  # seconds to wait for /health

HEALTH_URL="http://127.0.0.1:${WHISPER_PORT}/health"

log() { echo "[whisper] $*" >&2; }

is_healthy() {
    curl -sf --max-time 2 "$HEALTH_URL" >/dev/null 2>&1
}

read_pid() {
    [[ -f "$PID_FILE" ]] && cat "$PID_FILE" 2>/dev/null || true
}

is_pid_alive() {
    local pid="$1"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

# 1) Already healthy? nothing to do.
if is_healthy; then
    log "already running and healthy on port ${WHISPER_PORT}"
    exit 0
fi

# 2) PID file points at a live process but /health is down — probably mid-restart.
existing_pid="$(read_pid)"
if is_pid_alive "$existing_pid"; then
    log "stale instance pid=${existing_pid} detected, killing it"
    kill "$existing_pid" 2>/dev/null || true
    # give it a moment to release the port
    for _ in 1 2 3 4 5; do
        if is_healthy; then
            log "existing instance recovered"
            exit 0
        fi
        sleep 1
    done
    kill -9 "$existing_pid" 2>/dev/null || true
    rm -f "$PID_FILE"
fi

# 3) Pre-flight checks.
if ! command -v "$WHISPER_BIN" >/dev/null 2>&1; then
    log "ERROR: '${WHISPER_BIN}' not found in PATH"
    exit 1
fi
if [[ ! -f "$WHISPER_MODEL" ]]; then
    log "ERROR: model not found: ${WHISPER_MODEL}"
    exit 1
fi

# 4) Launch detached.
log "starting ${WHISPER_BIN} (model=${WHISPER_MODEL}, ${WHISPER_HOST}:${WHISPER_PORT})"
nohup "$WHISPER_BIN" \
    --model "$WHISPER_MODEL" \
    --host "$WHISPER_HOST" \
    --port "$WHISPER_PORT" \
    >"$LOG_FILE" 2>&1 &

new_pid=$!
echo "$new_pid" > "$PID_FILE"
log "spawned pid=${new_pid}, log=${LOG_FILE}"

# 5) Wait for /health to come up.
deadline=$((SECONDS + HEALTH_TIMEOUT))
while (( SECONDS < deadline )); do
    if is_healthy; then
        log "ready (pid=${new_pid})"
        exit 0
    fi
    # If the process already died, fail fast.
    if ! is_pid_alive "$new_pid"; then
        log "ERROR: process exited before becoming healthy. Tail of log:"
        tail -n 30 "$LOG_FILE" >&2 || true
        rm -f "$PID_FILE"
        exit 1
    fi
    sleep 1
done

log "ERROR: timed out after ${HEALTH_TIMEOUT}s waiting for ${HEALTH_URL}"
tail -n 30 "$LOG_FILE" >&2 || true
exit 1

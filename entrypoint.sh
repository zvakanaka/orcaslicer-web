#!/bin/sh
set -e

# Create profile directories
mkdir -p "${PROFILES_DIR}/printer" "${PROFILES_DIR}/process" "${PROFILES_DIR}/filament"
mkdir -p "${TEMP_DIR}"

# Start virtual X display
Xvfb :99 -screen 0 640x480x16 -ac +extension GLX +render -noreset &
sleep 1
export DISPLAY=:99

exec python3 /app/app.py

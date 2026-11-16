#!/usr/bin/env bash
# Starts the Codespace start page on :5170 (postCreateCommand and postStartCommand). Idempotent.
# Only the system python3 and the standard library, so it is up before pip and npm have run.
cd "$(dirname "$0")"
mkdir -p ../logs
if ! curl -sf http://127.0.0.1:5170/status >/dev/null 2>&1; then
  setsid nohup python3 server.py >../logs/startside.log 2>&1 </dev/null &
fi
exit 0

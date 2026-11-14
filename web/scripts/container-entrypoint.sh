#!/bin/sh
set -eu
case "${MOTOR_MODE:-control}" in
  control)
    exec uvicorn backend.app:app --host "${MOTOR_LISTEN_HOST:-0.0.0.0}" --port "${MOTOR_LISTEN_PORT:-8000}" --workers 1 --no-access-log --limit-concurrency 128 ;;
  demo)
    exec uvicorn backend.app:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log --limit-concurrency 128 ;;
  agent)
    exec python -m backend.agent_server ;;
  *) echo 'MOTOR_MODE must be control, demo or agent.' >&2; exit 2 ;;
esac

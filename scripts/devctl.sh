#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${ROOT_DIR}/.runtime"
BACKEND_PID_FILE="${RUNTIME_DIR}/backend.pid"
FRONTEND_PID_FILE="${RUNTIME_DIR}/frontend.pid"
BACKEND_LOG_FILE="${RUNTIME_DIR}/backend.log"
FRONTEND_LOG_FILE="${RUNTIME_DIR}/frontend.log"

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

usage() {
  cat <<USAGE
Usage: scripts/devctl.sh <start|stop|restart|status|logs> [all|backend|frontend]

Examples:
  scripts/devctl.sh start
  scripts/devctl.sh stop
  scripts/devctl.sh restart backend
  scripts/devctl.sh status
  scripts/devctl.sh logs frontend

Environment:
  BACKEND_HOST=${BACKEND_HOST}
  BACKEND_PORT=${BACKEND_PORT}
  FRONTEND_HOST=${FRONTEND_HOST}
  FRONTEND_PORT=${FRONTEND_PORT}
USAGE
}

ensure_runtime_dir() {
  mkdir -p "${RUNTIME_DIR}"
}

pid_is_running() {
  local pid_file="$1"
  [[ -s "${pid_file}" ]] || return 1
  local pid
  pid="$(cat "${pid_file}")"
  [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" 2>/dev/null
}

remove_stale_pid() {
  local pid_file="$1"
  if [[ -f "${pid_file}" ]] && ! pid_is_running "${pid_file}"; then
    rm -f "${pid_file}"
  fi
}

start_backend() {
  ensure_runtime_dir
  remove_stale_pid "${BACKEND_PID_FILE}"
  if pid_is_running "${BACKEND_PID_FILE}"; then
    echo "backend already running: pid $(cat "${BACKEND_PID_FILE}")"
    return
  fi

  cd "${ROOT_DIR}"
  nohup python3 -m uvicorn backend.app.main:app \
    --reload \
    --app-dir . \
    --host "${BACKEND_HOST}" \
    --port "${BACKEND_PORT}" \
    >"${BACKEND_LOG_FILE}" 2>&1 &
  echo "$!" >"${BACKEND_PID_FILE}"
  sleep 1
  if ! pid_is_running "${BACKEND_PID_FILE}"; then
    echo "backend failed to start, last log lines:"
    tail -n 40 "${BACKEND_LOG_FILE}" 2>/dev/null || true
    rm -f "${BACKEND_PID_FILE}"
    exit 1
  fi
  echo "backend started: pid $(cat "${BACKEND_PID_FILE}"), log ${BACKEND_LOG_FILE}"
}

start_frontend() {
  ensure_runtime_dir
  remove_stale_pid "${FRONTEND_PID_FILE}"
  if pid_is_running "${FRONTEND_PID_FILE}"; then
    echo "frontend already running: pid $(cat "${FRONTEND_PID_FILE}")"
    return
  fi

  cd "${ROOT_DIR}/frontend"
  nohup npm run dev -- --host "${FRONTEND_HOST}" --port "${FRONTEND_PORT}" \
    >"${FRONTEND_LOG_FILE}" 2>&1 &
  echo "$!" >"${FRONTEND_PID_FILE}"
  sleep 1
  if ! pid_is_running "${FRONTEND_PID_FILE}"; then
    echo "frontend failed to start, last log lines:"
    tail -n 40 "${FRONTEND_LOG_FILE}" 2>/dev/null || true
    rm -f "${FRONTEND_PID_FILE}"
    exit 1
  fi
  echo "frontend started: pid $(cat "${FRONTEND_PID_FILE}"), log ${FRONTEND_LOG_FILE}"
}

stop_process() {
  local name="$1"
  local pid_file="$2"

  remove_stale_pid "${pid_file}"
  if ! pid_is_running "${pid_file}"; then
    echo "${name} not running"
    rm -f "${pid_file}"
    return
  fi

  local pid
  pid="$(cat "${pid_file}")"
  pkill -TERM -P "${pid}" 2>/dev/null || true
  kill "${pid}" 2>/dev/null || true

  for _ in {1..20}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      rm -f "${pid_file}"
      echo "${name} stopped"
      return
    fi
    sleep 0.2
  done

  pkill -KILL -P "${pid}" 2>/dev/null || true
  kill -9 "${pid}" 2>/dev/null || true
  rm -f "${pid_file}"
  echo "${name} force stopped"
}

status_process() {
  local name="$1"
  local pid_file="$2"
  local log_file="$3"

  remove_stale_pid "${pid_file}"
  if pid_is_running "${pid_file}"; then
    echo "${name}: running, pid $(cat "${pid_file}"), log ${log_file}"
  else
    echo "${name}: stopped"
  fi
}

start_target() {
  local target="$1"
  case "${target}" in
    all)
      start_backend
      start_frontend
      ;;
    backend) start_backend ;;
    frontend) start_frontend ;;
    *) usage; exit 2 ;;
  esac
}

stop_target() {
  local target="$1"
  case "${target}" in
    all)
      stop_process frontend "${FRONTEND_PID_FILE}"
      stop_process backend "${BACKEND_PID_FILE}"
      ;;
    backend) stop_process backend "${BACKEND_PID_FILE}" ;;
    frontend) stop_process frontend "${FRONTEND_PID_FILE}" ;;
    *) usage; exit 2 ;;
  esac
}

status_target() {
  local target="$1"
  case "${target}" in
    all)
      status_process backend "${BACKEND_PID_FILE}" "${BACKEND_LOG_FILE}"
      status_process frontend "${FRONTEND_PID_FILE}" "${FRONTEND_LOG_FILE}"
      ;;
    backend) status_process backend "${BACKEND_PID_FILE}" "${BACKEND_LOG_FILE}" ;;
    frontend) status_process frontend "${FRONTEND_PID_FILE}" "${FRONTEND_LOG_FILE}" ;;
    *) usage; exit 2 ;;
  esac
}

logs_target() {
  local target="$1"
  case "${target}" in
    backend) tail -f "${BACKEND_LOG_FILE}" ;;
    frontend) tail -f "${FRONTEND_LOG_FILE}" ;;
    all)
      echo "Use one target for logs: backend or frontend"
      exit 2
      ;;
    *) usage; exit 2 ;;
  esac
}

command="${1:-}"
target="${2:-all}"

case "${command}" in
  start) start_target "${target}" ;;
  stop) stop_target "${target}" ;;
  restart)
    stop_target "${target}"
    start_target "${target}"
    ;;
  status) status_target "${target}" ;;
  logs) logs_target "${target}" ;;
  -h|--help|help|"") usage ;;
  *) usage; exit 2 ;;
esac

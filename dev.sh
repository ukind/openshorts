#!/usr/bin/env bash
# ============================================================================
# dev.sh — run the full OpenShorts stack natively (Windows Git Bash / POSIX)
#
#   ./dev.sh                 backend + frontend + renderer
#   ./dev.sh --no-renderer   skip the Remotion renderer (no AI Shorts compositing)
#   ./dev.sh --yes           auto-confirm (kill stale listeners without asking)
#   ./dev.sh --check         preflight + port report only, launch nothing
#   ./dev.sh stop [--yes]    kill any services from a previous run (interactive
#                            prompt by default, silent with --yes)
#
# Robustness:
#   - preflight: venv, node, npm, ffmpeg, node_modules (auto npm ci if missing)
#   - stale listeners on 8000/5173/3100 are killed — but ONLY if the owning
#     image is python.exe or node.exe (never some unrelated process)
#   - frontend waits for the backend's /health before starting
#   - each service logs to output/dev/<svc>.log and to the console with a
#     color prefix; log files are re-tailed so prefixes stay attached
#   - a watchdog flags any service that dies after having been up
#   - Ctrl+C kills every service tree (console event + taskkill /T backstop);
#     for scripts and edge cases, ./dev.sh stop frees the three ports
#   - if a previous run was killed hard, the next start auto-recovers the ports
# ============================================================================

set -u -o pipefail

# ---------------------------------------------------------------- config ----
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$REPO_ROOT/output/dev"
BACKEND_PORT=8000
FRONTEND_PORT=5173
RENDERER_PORT=3100
BACKEND_URL="http://127.0.0.1:$BACKEND_PORT"

RUN_RENDERER=1
ASSUME_YES=0
CHECK_ONLY=0
MODE_STOP=0
for arg in "$@"; do
  case "$arg" in
    --stop|stop)  MODE_STOP=1 ;;
    -h|--help)        sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --no-renderer)    RUN_RENDERER=0 ;;
    -y|--yes)         ASSUME_YES=1 ;;
    --check)          CHECK_ONLY=1 ;;
    *) echo "unknown flag: $arg (see --help)"; exit 2 ;;
  esac
done

# ---------------------------------------------------------------- output ----
if [ -t 1 ]; then
  C_BACK=$'\033[36m'; C_FRONT=$'\033[35m'; C_REND=$'\033[33m'
  C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_BAD=$'\033[31m'; C_DIM=$'\033[2m'; C_0=$'\033[0m'
else
  C_BACK=""; C_FRONT=""; C_REND=""; C_OK=""; C_WARN=""; C_BAD=""; C_DIM=""; C_0=""
fi
info()  { printf '%s\n' "${C_OK}[dev]${C_0} $*"; }
warn()  { printf '%s\n' "${C_WARN}[dev]${C_0} $*" >&2; }
die()   { printf '%s\n' "${C_BAD}[dev] $*${C_0}" >&2; exit 1; }

# ------------------------------------------------------------- preflight ----
PY=""
[ -f "$REPO_ROOT/.venv/Scripts/python.exe" ] && PY="$REPO_ROOT/.venv/Scripts/python.exe"
[ -z "$PY" ] && [ -x "$REPO_ROOT/.venv/bin/python" ] && PY="$REPO_ROOT/.venv/bin/python"
command -v node >/dev/null 2>&1 || die "node not found on PATH"
command -v npm  >/dev/null 2>&1 || die "npm not found on PATH"
command -v ffmpeg >/dev/null 2>&1 || warn "ffmpeg not on PATH — backend pipeline needs it"


if [ -z "$PY" ]; then
  die "no .venv found. Create it first:
  uv venv .venv --python 3.11
  uv pip install --python .venv/Scripts/python.exe -r requirements.txt -r requirements-billing.txt pytest"
fi
[ -f "$REPO_ROOT/.env" ] || warn ".env missing — backend runs on defaults (cp .env.example .env to configure)"

ensure_node_modules() {  # ensure_node_modules <dir> <label>
  if [ ! -d "$REPO_ROOT/$1/node_modules" ]; then
    info "installing npm deps for $2 (first run)"
    ( cd "$REPO_ROOT/$1" && { npm ci || npm install; } ) || die "npm install failed in $1"
  fi
}
[ "$CHECK_ONLY" = 0 ] && ensure_node_modules dashboard  "frontend"
ensure_node_modules remotion    "remotion (needed by renderer bundle)"
[ "$CHECK_ONLY" = 0 ] && [ "$RUN_RENDERER" = 1 ] && ensure_node_modules render-service "renderer"

# ------------------------------------------------------------------ ports ---
win_taskkill_tree() {  # win_taskkill_tree <pid> — kill a process and its children
  MSYS_NO_PATHCONV=1 taskkill /F /T /PID "$1" >/dev/null 2>&1 || kill "$1" 2>/dev/null || true
}
win_image_of() {  # win_image_of <pid> -> image name, e.g. python.exe (empty on error)
  MSYS_NO_PATHCONV=1 tasklist /FI "PID eq $1" /NH 2>/dev/null | awk 'NF {print $1; exit}' | tr -d '"' | tr 'A-Z' 'a-z'
}
sweep_family() {  # sweep_family -- kill leftover tsx/vite/npm respawners bound to this repo
  powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='node.exe' or Name='cmd.exe'\" | Where-Object { \$_.CommandLine -match 'openshorts' } | ForEach-Object { \$_ | Remove-CimInstance }" >/dev/null 2>&1
  return 0
}

port_pids() {  # port_pids <port> -> space-separated PIDs listening on that port
  netstat -ano 2>/dev/null | awk -v p=":$1" '$1=="TCP" && $4=="LISTENING" && $2 ~ p"$" {print $5}' | sort -u
}
is_listening() { [ -n "$(port_pids "$1")" ]; }

# kill_listeners <port> <label> — free a port; only kills python/node images
kill_listeners() {
  local port="$1" label="$2" pid img killed=0 reply
  local pids; pids="$(port_pids "$port")"
  [ -z "$pids" ] && return 0
  for pid in $pids; do
    img="$(win_image_of "$pid")"
    case "$img" in
      python.exe|node.exe)
        if [ "$ASSUME_YES" = 1 ]; then reply=y
        elif [ -t 0 ]; then
          printf '%s\n' "${C_WARN}[dev]${C_0} port $port ($label) is held by stale PID $pid ($img). Kill it? [y/N]"
          read -r reply || reply=n
        else reply=n; fi
        if [[ "$reply" =~ ^[Yy] ]]; then
          win_taskkill_tree "$pid"; killed=1
          info "killed stale $label (pid $pid) on port $port"
        else
          warn "port $port still busy — $label will be SKIPPED"
        fi ;;
      *) warn "port $port held by PID $pid ($img) — not python/node, refusing to kill; $label will be SKIPPED" ;;
    esac
  done
  [ "$killed" = 1 ] && sleep 1
  return 0
}

if [ "$MODE_STOP" = 1 ]; then
  for port in $BACKEND_PORT $FRONTEND_PORT $RENDERER_PORT; do
    kill_listeners "$port" "port $port"
  done
  info "ports freed"
  sweep_family
  exit 0
fi

if [ "$CHECK_ONLY" = 1 ]; then
  info "python:   $PY"
  info ".env:     $([ -f "$REPO_ROOT/.env" ] && echo present || echo MISSING)"
  for port in $BACKEND_PORT $FRONTEND_PORT $RENDERER_PORT; do
    if is_listening "$port"; then
      status="${C_BAD}BUSY (pid $(port_pids "$port"))${C_0}"
    else
      status="${C_OK}free${C_0}"
    fi
    info "port $port: $status"
  done
  info "pre-flight OK"
  exit 0
fi

# ------------------------------------------------------------- tail feed ----
TAIL_PIDS=()
tail_log() {  # tail_log <file> <colorprefix> — live stream of svc log, prefixed
  tail -F "$1" 2>/dev/null | \
    awk -v c="$2" -v r="$C_0" '{printf "%s%s%s\n", c, $0, r; fflush()}' &
  TAIL_PIDS+=("$!")
}
cleanup() {
  trap - EXIT INT TERM
  # kill the tracked service trees FIRST (uvicorn / npm->node / tsx->node chains)
  for p in "${BACKEND_PID:-}" "${RENDERER_PID:-}" "${FRONTEND_PID:-}"; do
    case "$p" in ''|*[!0-9]*) ;; *) win_taskkill_tree "$p" ;; esac
  done
  kill "${TAIL_PIDS[@]}" 2>/dev/null
  # backstop: anything still listening on our ports that is python/node
  for port in $BACKEND_PORT $FRONTEND_PORT $RENDERER_PORT; do
    for pid in $(port_pids "$port"); do
      case "$(win_image_of "$pid")" in
        python.exe|node.exe) win_taskkill_tree "$pid" ;;
      esac
    done
  done
  wait 2>/dev/null
  printf '%s\n' "${C_DIM}[dev] all services stopped${C_0}"
}
trap cleanup EXIT            # runs on every exit path
trap 'exit 130' INT      # Ctrl+C: set code, EXIT trap does the cleanup
trap 'exit 143' TERM
cd "$REPO_ROOT"
# ---------------------------------------------------------------- launch ----
mkdir -p "$LOG_DIR"
RENDERER_PORT=$RENDERER_PORT

kill_listeners "$BACKEND_PORT" "backend"
BACKEND_UP_SEEN=0
if is_listening "$BACKEND_PORT"; then
  warn "backend SKIPPED — port $BACKEND_PORT busy"
else
  backlog="$LOG_DIR/backend.log"; : > "$backlog"
  info "backend  ${C_DIM}→ http://localhost:$BACKEND_PORT   (docs: /docs)${C_0}"
  env PYTHONUTF8=1 PYTHONUNBUFFERED=1 "$PY" -m uvicorn app:app \
    --host 0.0.0.0 --port "$BACKEND_PORT" >> "$backlog" 2>&1 &
  BACKEND_PID=$!
  tail_log "$backlog" "$C_BACK[backend] "
fi

kill_listeners "$RENDERER_PORT" "renderer"
if [ "$RUN_RENDERER" = 1 ] && is_listening "$RENDERER_PORT"; then
  warn "renderer SKIPPED — port $RENDERER_PORT busy"
  RUN_RENDERER=0
fi
if [ "$RUN_RENDERER" = 1 ]; then
  rendlog="$LOG_DIR/renderer.log"; : > "$rendlog"
  info "renderer ${C_DIM}→ http://localhost:$RENDERER_PORT${C_0}"
  (
    cd "$REPO_ROOT/render-service" &&
    env PORT="$RENDERER_PORT" OUTPUT_DIR="$REPO_ROOT/output" \
        REMOTION_BUNDLE_PATH="$REPO_ROOT/remotion" npm run dev >> "$rendlog" 2>&1
  ) &
  RENDERER_PID=$!
  tail_log "$rendlog" "$C_REND[renderer]"
fi

# gate: wait for backend /health before the frontend starts proxying to it
if [ "${BACKEND_PID:-}" != "" ]; then
  printf '%s' "${C_DIM}[dev] waiting for backend /health"
  backend_ready=0
  for _ in $(seq 1 90); do
    if curl -s -o /dev/null --max-time 2 "$BACKEND_URL/health"; then backend_ready=1; break; fi
    printf '.'; sleep 1
  done
  printf '\n'
  if [ "$backend_ready" = 1 ]; then info "backend is up"; else
    warn "backend did not answer /health in 90s — starting frontend anyway (check output/dev/backend.log)"
  fi
fi

kill_listeners "$FRONTEND_PORT" "frontend"
if is_listening "$FRONTEND_PORT"; then
  warn "frontend SKIPPED — port $FRONTEND_PORT busy"
else
  frontlog="$LOG_DIR/frontend.log"; : > "$frontlog"
  info "frontend ${C_DIM}→ http://localhost:$FRONTEND_PORT — click Launch App, then Settings to add API keys${C_0}"
  (
    cd "$REPO_ROOT/dashboard" &&
    VITE_PROXY_TARGET="$BACKEND_URL" VITE_RENDER_TARGET="http://localhost:$RENDERER_PORT" \
      npm run dev >> "$frontlog" 2>&1
  ) &
  FRONTEND_PID=$!
  tail_log "$frontlog" "$C_FRONT[frontend]"
fi

# -------------------------------------------------------------- watchdog ----
declare -A SEEN    # port -> 1 once the service has listened at least once
declare -A DOWN    # consecutive down-checks, per port
ports_enabled="$BACKEND_PORT $FRONTEND_PORT"
[ "$RUN_RENDERER" = 1 ] || true
is_port_rendered() { [ "$RUN_RENDERER" = 1 ] && [ "$1" = "$RENDERER_PORT" ]; }
[ "$RUN_RENDERER" = 1 ] && ports_enabled="$ports_enabled $RENDERER_PORT"

info "dev stack running — Ctrl+C to stop everything"
while :; do
  sleep 2
  alive_any=0
  for port in $ports_enabled; do
    if is_listening "$port"; then
      SEEN[$port]=1; DOWN[$port]=0; alive_any=1
    elif [ "${SEEN[$port]:-0}" = 1 ]; then
      DOWN[$port]=$(( ${DOWN[$port]:-0} + 1 ))
      if [ "${DOWN[$port]}" -ge 3 ]; then
        case "$port" in
          "$BACKEND_PORT")   svc=backend;  logf=backend.log ;;
          "$FRONTEND_PORT")  svc=frontend; logf=frontend.log ;;
          *)                 svc=renderer; logf=renderer.log ;;
        esac
        warn "$svc exited — full log: output/dev/$logf"
        DOWN[$port]=-999            # report once
      fi
    fi
  done
  # all enabled ports down (and at least one had been up) -> the stack is gone
  ever_up=0; now_up=0
  for port in $ports_enabled; do
    [ "${SEEN[$port]:-0}" = 1 ] && ever_up=1 && { is_listening "$port" && now_up=1; }
  done
  if [ "$ever_up" = 1 ] && [ "$now_up" = 0 ]; then cleanup; exit 1; fi
done
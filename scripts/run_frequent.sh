#!/bin/bash

# Zona horaria del usuario (ver explicacion en run_daily.sh).
export TZ='America/Argentina/Buenos_Aires'

# --- Configuración y Rutas ---
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( dirname "$SCRIPT_DIR" )"
VENV_PATH="$PROJECT_ROOT/venv"
LOG_FILE="$PROJECT_ROOT/reports/cron_frequent_log.txt"
LOCK_FILE="/tmp/el_industrial.lock"
HOSTNAME=$(hostname)

# Función para loggear con timestamp
log_message() {
    # Logging minimalista para no llenar el disco con ejecuciones silenciosas.
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$HOSTNAME] $1" >> "$LOG_FILE"
}

# --- Sistema de Bloqueo ---
if [ -f "$LOCK_FILE" ]; then
    exit 1
fi
touch "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# --- Cargar Variables de Entorno ---
if [ -f "$PROJECT_ROOT/.env" ]; then
    export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs)
fi

cd "$PROJECT_ROOT" || exit

# --- Auto-pull: siempre ejecutar la ultima version ---
# Mismo self-heal que run_daily: si hay .gz untracked bloqueando, los limpia y reintenta.
if ! git pull --rebase --autostash origin main --quiet 2>>"$LOG_FILE"; then
    PULL_ERR=$(git pull --rebase --autostash origin main 2>&1 || true)
    BLOCKING=$(echo "$PULL_ERR" | grep -oP 'tenants/[^\s]+\.gz' || true)
    if [ -n "$BLOCKING" ]; then
        log_message "SELFHEAL frequent: limpiando .gz untracked bloqueante..."
        while IFS= read -r f; do
            [ -f "$PROJECT_ROOT/$f" ] && rm -f "$PROJECT_ROOT/$f" && log_message "SELFHEAL frequent: rm $f"
        done <<< "$BLOCKING"
        git pull --rebase --autostash origin main --quiet >>"$LOG_FILE" 2>&1 || \
            log_message "ADVERTENCIA: git pull fallo incluso tras cleanup en frequent."
    else
        log_message "ADVERTENCIA: git pull fallo en frequent (no es .gz). Continuando con codigo local."
    fi
fi

# --- Auto-install deps si requirements.txt cambio (igual logica que run_daily) ---
REQ_FILE="$PROJECT_ROOT/requirements.txt"
HASH_FILE="$PROJECT_ROOT/status/.deps_hash"
if [ -f "$REQ_FILE" ] && [ -d "$VENV_PATH" ]; then
    mkdir -p "$PROJECT_ROOT/status"
    CURRENT_HASH=$(sha256sum "$REQ_FILE" 2>/dev/null | awk '{print $1}')
    STORED_HASH=$(cat "$HASH_FILE" 2>/dev/null || echo "")
    if [ "$CURRENT_HASH" != "$STORED_HASH" ]; then
        log_message "requirements.txt cambio. Reinstalando deps en venv..."
        if "$VENV_PATH/bin/pip" install -q -r "$REQ_FILE" >>"$LOG_FILE" 2>&1; then
            echo "$CURRENT_HASH" > "$HASH_FILE"
            log_message "Deps reinstaladas OK."
        fi
    fi
fi

# --- Evitar ejecución si el nodo ya está actualizado (opcional para frecuente) ---
# Si queremos que el frecuente siga corriendo para capturar telemetría, no lo bloqueamos por fecha.
# Pero sí debemos evitar que corra si el lock file local existe.

# --- Chequear Reintento Inteligente ---
# Si la corrida diaria falló hoy por culpa del proveedor y aún no se recuperó,
# promovemos la ejecución frecuente a una corrida completa diaria (run_daily.sh).
if python3 "$SCRIPT_DIR/should_retry.py" >> "$LOG_FILE" 2>&1; then
    log_message "REINTENTO: Detección de fallo previo. Ejecutando ciclo completo diario..."
    rm -f "$LOCK_FILE"
    bash "$SCRIPT_DIR/run_daily.sh"
    exit $?
fi

# --- Ejecucion Silenciosa ---
if [ -d "$VENV_PATH" ]; then
    source "$VENV_PATH/bin/activate"
fi

# Capturar stderr para que errores de importacion o crashes no se pierdan.
FREQ_STDERR=$(mktemp)
python3 "$SCRIPT_DIR/update_products.py" --silent >>"$LOG_FILE" 2>"$FREQ_STDERR"
PY_EXIT_CODE=$?
if [ -s "$FREQ_STDERR" ]; then
    log_message "--- update_products --silent stderr ---"
    cat "$FREQ_STDERR" >>"$LOG_FILE"
    log_message "--- fin stderr ---"
fi
rm -f "$FREQ_STDERR"

if [ $PY_EXIT_CODE -eq 0 ]; then
    # Subir metricas y heartbeat incluso en ejecuciones frecuentes
    git add status/heartbeat.json status/metrics.jsonl 2>/dev/null
    if [[ -n $(git status -s status/) ]]; then
        git commit -m "Telemetria frecuente: $(date +%H:%M) [$HOSTNAME] [skip ci]" --quiet
        git push origin main --quiet
    fi
else
    log_message "ERROR: Ejecucion frecuente fallo con codigo $PY_EXIT_CODE."
    # Lanzar watchdog: si los datos estan stale, intentara remediacion automatica.
    log_message "Lanzando selfheal_watchdog para verificar estado y remediar..."
    python3 "$SCRIPT_DIR/selfheal_watchdog.py" >>"$LOG_FILE" 2>&1 || true
    exit 1
fi

exit 0

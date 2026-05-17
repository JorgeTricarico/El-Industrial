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
# rebase + autostash para no fallar si quedaron cambios locales (metrics, heartbeat).
if ! git pull --rebase --autostash origin main --quiet 2>>"$LOG_FILE"; then
    log_message "ADVERTENCIA: git pull fallo en frequent, continuando con codigo local."
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

# --- Ejecución Silenciosa ---
if [ -d "$VENV_PATH" ]; then
    source "$VENV_PATH/bin/activate"
fi

python3 "$SCRIPT_DIR/update_products.py" --silent
PY_EXIT_CODE=$?

if [ $PY_EXIT_CODE -eq 0 ]; then
    # Subir métricas y heartbeat incluso en ejecuciones frecuentes
    git add status/heartbeat.json status/metrics.jsonl 2>/dev/null
    if [[ -n $(git status -s status/) ]]; then
        git commit -m "Telemetría frecuente: $(date +%H:%M) [$HOSTNAME] [skip ci]" --quiet
        git push origin main --quiet
    fi
else
    log_message "ERROR: Ejecución frecuente falló con código $PY_EXIT_CODE."
    exit 1
fi

exit 0

#!/bin/bash

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

# --- Ejecución Silenciosa ---
if [ -d "$VENV_PATH" ]; then
    source "$VENV_PATH/bin/activate"
fi

python3 "$SCRIPT_DIR/update_products.py" --silent
PY_EXIT_CODE=$?

if [ $PY_EXIT_CODE -ne 0 ]; then
    log_message "ERROR: Ejecución frecuente falló con código $PY_EXIT_CODE."
    exit 1
fi

exit 0

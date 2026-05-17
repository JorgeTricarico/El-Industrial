#!/bin/bash

# --- Configuración y Rutas ---
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( dirname "$SCRIPT_DIR" )"
VENV_PATH="$PROJECT_ROOT/venv"
LOG_FILE="$PROJECT_ROOT/reports/cron_log.txt"
LOCK_FILE="/tmp/el_industrial.lock"
FILE_DATE=$(date +%y-%m-%d)
HOSTNAME=$(hostname)

# Función para loggear con timestamp
log_message() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$HOSTNAME] $1" | tee -a "$LOG_FILE"
}

# --- Sistema de Bloqueo (Evitar ejecuciones simultáneas) ---
if [ -f "$LOCK_FILE" ]; then
    log_message "ERROR: Ya hay una instancia corriendo o el lockfile quedó huérfano. Abortando."
    exit 1
fi
touch "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

log_message "Iniciando proceso diario de actualización..."

# --- Cargar Variables de Entorno ---
if [ -f "$PROJECT_ROOT/.env" ]; then
    export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs)
fi

cd "$PROJECT_ROOT" || exit

# --- Auto-pull: siempre ejecutar la ultima version del codigo ---
# Hacemos pull --rebase --autostash antes que cualquier otra logica para que
# todos los nodos arranquen con el mismo arbol que main en GitHub.
# Si el pull falla (conflicto, no hay internet), seguimos con lo que haya
# para no romper la corrida nocturna; el healthcheck nos avisara.
log_message "Auto-pull: git pull --rebase --autostash origin main..."
if git pull --rebase --autostash origin main --quiet 2>>"$LOG_FILE"; then
    NEW_HEAD=$(git rev-parse --short HEAD 2>/dev/null)
    log_message "Pull OK. HEAD=$NEW_HEAD"
else
    log_message "ADVERTENCIA: git pull fallo, continuando con codigo local."
fi

# --- Auto-install deps si requirements.txt cambio ---
# Cada nodo guarda el hash del requirements.txt instalado en status/.deps_hash.
# Si el hash actual difiere (alguien actualizo el archivo y pusheo), reinstalamos
# para que git pull + nueva libreria funcionen sin intervencion manual.
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
        else
            log_message "ERROR: pip install fallo. Continuando con deps existentes."
        fi
    fi
fi

# --- Dedup remoto vía commit-marker ---
# Cualquier nodo (Pi, Mint o GH Actions) marca su corrida con el tag [run:YY-MM-DD]
# en el commit. Antes de ejecutar, verificamos si ya hay un commit de hoy.
TODAY_TAG="[run:$FILE_DATE]"
LAST_COMMIT_MSG=$(git log origin/main --format=%s -5 2>/dev/null || echo "")
if echo "$LAST_COMMIT_MSG" | grep -qF "$TODAY_TAG"; then
    log_message "Otro nodo ya ejecuto hoy ($TODAY_TAG). Saliendo limpio."
    exit 0
fi

# --- Rol del nodo: primary o backup ---
# Se configura en .env de cada nodo (EL_INDUSTRIAL_ROLE=primary|backup).
# Fallback: hostname contiene "mint" => backup (compatibilidad con setups viejos).
ROLE="${EL_INDUSTRIAL_ROLE:-}"
if [ -z "$ROLE" ]; then
    if [[ "${HOSTNAME,,}" == *"mint"* ]]; then
        ROLE="backup"
    else
        ROLE="primary"
    fi
fi
log_message "Rol del nodo: $ROLE"

# --- Logica de Nodo Secundario (Backup): verificacion adicional via raw URL ---
if [ "$ROLE" = "backup" ]; then
    log_message "Nodo Secundario. Verificando archivo de datos en GitHub..."
    URL="https://raw.githubusercontent.com/JorgeTricarico/El-Industrial/main/data/lista_precio_${FILE_DATE}_json_compres.gz"
    if curl --output /dev/null --silent --head --fail "$URL"; then
        log_message "El archivo ya existe en GitHub (Nodo Principal OK). Finalizando sin cambios."
        exit 0
    fi
    log_message "AVISO: No se encontro el archivo de hoy en GitHub. Procediendo como backup..."
fi

# --- Ejecución del Script de Python ---
log_message "Activando entorno virtual y ejecutando update_products.py..."
if [ -d "$VENV_PATH" ]; then
    source "$VENV_PATH/bin/activate"
fi

python3 "$SCRIPT_DIR/update_products.py"
PY_EXIT_CODE=$?

if [ $PY_EXIT_CODE -ne 0 ]; then
    log_message "CRÍTICO: El script de Python falló con código $PY_EXIT_CODE."
    exit 1
fi

log_message "Sincronizando tenants (front + data mirror para testing)..."
python3 "$SCRIPT_DIR/sync_tenants.py" >>"$LOG_FILE" 2>&1 || log_message "ADVERTENCIA: sync_tenants fallo, continuando."

log_message "Ejecutando reporte ejecutivo nocturno..."
python3 "$SCRIPT_DIR/nightly_report.py"
NR_EXIT_CODE=$?
if [ $NR_EXIT_CODE -eq 0 ]; then
    log_message "Nightly OK (exit=0). Telegram deberia haber recibido el informe."
else
    log_message "ADVERTENCIA: nightly_report.py salio con codigo $NR_EXIT_CODE. Revisar status/metrics.jsonl."
fi

# --- No pushear si es un nodo de backup ---
if [ "$ROLE" = "backup" ]; then
    log_message "Nodo Secundario (Backup): No se realizará push a GitHub para evitar conflictos."
    exit 0
fi

# --- Gestión de Git (con Reintentos) ---
log_message "Procesando cambios en Git..."

# Solo si hay cambios reales para commitear
if [[ -n $(git status -s) ]]; then
    if [ ! -z "$GITHUB_TOKEN" ]; then
        git remote set-url origin "https://x-access-token:${GITHUB_TOKEN}@github.com/JorgeTricarico/El-Industrial.git"
    fi
    
    git add .
    git commit -m "Actualizacion automatica de precios: $(date +%d/%m/%Y) [$HOSTNAME] $TODAY_TAG [skip ci]"
    
    # Intento de Push con reintentos
    MAX_RETRIES=3
    RETRY_COUNT=0
    PUSH_SUCCESS=false
    
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        log_message "Intento de push $RETRY_COUNT de $MAX_RETRIES..."
        git push origin HEAD:main
        if [ $? -eq 0 ]; then
            PUSH_SUCCESS=true
            log_message "Push exitoso a GitHub."
            break
        else
            log_message "Fallo en el push. Esperando 30 segundos..."
            sleep 30
            let RETRY_COUNT=RETRY_COUNT+1
        fi
    done
    
    if [ "$PUSH_SUCCESS" = false ]; then
        log_message "ERROR CRÍTICO: No se pudieron subir los cambios a GitHub tras $MAX_RETRIES intentos."
        # Aquí podrías añadir una llamada a un script de notificación de error vía Telegram si quisieras
        exit 1
    fi
else
    log_message "No se detectaron cambios en el repositorio. Nada que subir."
fi

log_message "Proceso finalizado correctamente."
exit 0


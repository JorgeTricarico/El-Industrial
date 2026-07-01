#!/bin/bash

# --- Zona horaria del usuario ---
# La Pi y los runners de GH Actions corren en UTC. Forzamos AR para que TODOS
# los timestamps que se loguean o se muestran al cliente (cron_log, Telegram,
# heartbeat, metrics) salgan en hora de Buenos Aires. Tanto `date` como
# Python heredan esta variable y la usan al llamar datetime.now().
export TZ='America/Argentina/Buenos_Aires'

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

# PULSE_PY se define ANTES del pull para que el bloque pull_fail pueda usarlo.
# BUG-FIX 31-may-2026: antes estaba definido en linea 101, despues del pull,
# causando que el pulse pull_fail nunca se ejecutara (variable vacia).
PULSE_PY="$SCRIPT_DIR/node_pulse.py"


# Hacemos pull --rebase --autostash antes que cualquier otra logica para que
# todos los nodos arranquen con el mismo arbol que main en GitHub.
# Si falla por archivos untracked conocidos (.gz de data), los limpiamos y
# reintentamos UNA VEZ antes de abortar (fix loop de death 29-may-2026).
log_message "Auto-pull: git pull --rebase --autostash origin main..."
PULL_ERR_FILE=$(mktemp)
# IMPORTANTE: NO usar `| tee` aca. El `if` evalua el exit del ULTIMO comando del
# pipe (tee, que siempre sale 0), enmascarando un fallo de git pull. Sin pipefail
# eso hacia que el script siguiera con codigo stale en vez de abortar (exit 2) —
# la causa raiz del bug de data vieja (19-may / 2-dias). Redirigimos directo al
# log para que el `if` vea el exit real de git. (fix 2026-07-01)
if git pull --rebase --autostash origin main --quiet >>"$LOG_FILE" 2>"$PULL_ERR_FILE"; then
    rm -f "$PULL_ERR_FILE"
    NEW_HEAD=$(git rev-parse --short HEAD 2>/dev/null)
    log_message "Pull OK. HEAD=$NEW_HEAD"
    python3 "$SCRIPT_DIR/refresh_heartbeat.py" >>"$LOG_FILE" 2>&1 || true
else
    PULL_STDERR=$(cat "$PULL_ERR_FILE")
    cat "$PULL_ERR_FILE" >>"$LOG_FILE"
    rm -f "$PULL_ERR_FILE"

    # --- Self-heal: limpiar .gz untrackeados que bloquean el merge ---
    # El error tipico es: "untracked working tree files would be overwritten by merge"
    # Esos archivos YA estan en origin/main — eliminarlos localmente es seguro.
    UNTRACKED_BLOCKING=$(echo "$PULL_STDERR" | grep -oP 'tenants/[^\s]+\.gz' || true)
    if [ -n "$UNTRACKED_BLOCKING" ]; then
        log_message "SELFHEAL: pull fallo por untracked .gz. Limpiando y reintentando..."
        while IFS= read -r blocked_file; do
            if [ -f "$PROJECT_ROOT/$blocked_file" ]; then
                log_message "SELFHEAL: eliminando untracked $blocked_file"
                rm -f "$PROJECT_ROOT/$blocked_file"
                # Loggear el evento en metrics.jsonl para trazabilidad
                python3 -c "
import json, os
from datetime import datetime
entry = {'ts': datetime.now().isoformat(), 'node': '$HOSTNAME', 'event': 'selfheal_git_cleanup', 'detail': 'removed untracked: $blocked_file'}
with open('$PROJECT_ROOT/status/metrics.jsonl', 'a') as f: f.write(json.dumps(entry) + chr(10))
" 2>/dev/null || true
            fi
        done <<< "$UNTRACKED_BLOCKING"
        log_message "SELFHEAL: reintentando git pull tras cleanup..."
        if git pull --rebase --autostash origin main --quiet >>"$LOG_FILE" 2>&1; then
            NEW_HEAD=$(git rev-parse --short HEAD 2>/dev/null)
            log_message "SELFHEAL: Pull OK tras cleanup. HEAD=$NEW_HEAD"
            python3 "$SCRIPT_DIR/refresh_heartbeat.py" >>"$LOG_FILE" 2>&1 || true
        else
            log_message "CRITICO: git pull fallo incluso tras cleanup. Abortando."
            PY_BIN="${VENV_PATH}/bin/python" && [ -x "$PY_BIN" ] || PY_BIN="python3"
            "$PY_BIN" "$PULSE_PY" --outcome "pull_fail" --note "pull fallo tras selfheal cleanup" >>"$LOG_FILE" 2>&1 || true
            "$PY_BIN" "$SCRIPT_DIR/aiops_remediate.py" "pull_fail tras selfheal: $PULL_STDERR" >>"$LOG_FILE" 2>&1 || true
            exit 2
        fi
    else
        log_message "CRITICO: git pull fallo (no es problema de .gz untracked). Abortando."
        log_message "Stderr: $PULL_STDERR"
        PY_BIN="${VENV_PATH}/bin/python" && [ -x "$PY_BIN" ] || PY_BIN="python3"
        "$PY_BIN" "$PULSE_PY" --outcome "pull_fail" --note "git pull fallo: $(echo $PULL_STDERR | head -c 200)" >>"$LOG_FILE" 2>&1 || true
        "$PY_BIN" "$SCRIPT_DIR/aiops_remediate.py" "git pull fallo: $PULL_STDERR" >>"$LOG_FILE" 2>&1 || true
        exit 2
    fi
fi

# --- Pulso del nodo: arrancamos. Siempre pulsea aunque la corrida no haga trabajo util.
# Trazabilidad total: cada device en cada cron deja huella en heartbeat.json.
# NOTA: PULSE_PY se define antes del bloque pull (ver arriba) para que pull_fail lo use.
pulse() {
    # uso: pulse <outcome> [nota]
    if [ -f "$PULSE_PY" ]; then
        "${VENV_PATH}/bin/python" "$PULSE_PY" --outcome "$1" --note "${2:-}" >>"$LOG_FILE" 2>&1 || true
    fi
}
pulse "started" "cron run inicio"

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

# --- Dedup remoto vía commit-marker (DISCRIMINADO) ---
# Cualquier nodo (Pi, Mint o GH Actions) marca su corrida con el tag [run:YY-MM-DD].
# PERO: el cloud last_resort + watchdogs commitean con el mismo tag aunque solo
# manden filler Telegram SIN actualizar precios. Si tomamos cualquier [run:YY-MM-DD]
# como "ya actualizado", el filler engaña al primary y nos quedamos sin precios.
# Lección 19/20-may-2026: cloud filler commit -> Pi vio el tag -> dup_skip -> 2 dias
# sin actualizar precios en prod.
# Regla: solo cuentan commits cuyo subject empieza con "Actualizacion automatica"
# (los commits reales de update_products). Fillers/pulses se ignoran.
TODAY_TAG="[run:$FILE_DATE]"
REAL_UPDATE_TODAY=$(git log origin/main --format=%s -20 2>/dev/null | \
    grep -F "$TODAY_TAG" | grep -c "^Actualizacion automatica" || true)
if [ "${REAL_UPDATE_TODAY:-0}" -gt 0 ]; then
    log_message "Otro nodo ya actualizo precios hoy ($TODAY_TAG). Pulso dup_skip y salgo."
    pulse "dup_skip" "commit-marker $TODAY_TAG con update real ya presente"
    # Push del heartbeat asi otros nodos ven el pulso.
    git add status/heartbeat.json 2>/dev/null || true
    if ! git diff --cached --quiet 2>/dev/null; then
        git commit -m "chore: pulse $HOSTNAME dup_skip [skip ci]" >>"$LOG_FILE" 2>&1 || true
        git push origin HEAD:main >>"$LOG_FILE" 2>&1 || true
    fi
    exit 0
fi

# --- Rol del nodo: primary o backup ---
# Fuente de verdad: infra/nodes.yml (via node_pulse.effective_role), con
# override por env EL_INDUSTRIAL_ROLE y fallback legacy (hostname "mint").
# Antes cualquier host que no dijera "mint" se auto-elegia primary — un backup
# mal registrado (DESKTOP-MI43BOU) se creia primary y pegaba a Bertual de
# madrugada generando ruido supplier_down (fix 2026-07-01).
ROLE="${EL_INDUSTRIAL_ROLE:-}"
if [ -z "$ROLE" ] && [ -f "$PULSE_PY" ]; then
    ROLE=$("${VENV_PATH}/bin/python" "$PULSE_PY" --resolve-role 2>/dev/null || true)
fi
if [ -z "$ROLE" ]; then
    # Ultima red si python/venv no esta disponible: hostname "mint" => backup.
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
        log_message "El archivo ya existe en GitHub (Nodo Principal OK). Pulso dup_skip y salgo."
        pulse "dup_skip" "primary ya genero $URL"
        git add status/heartbeat.json 2>/dev/null || true
        if ! git diff --cached --quiet 2>/dev/null; then
            git commit -m "chore: pulse $HOSTNAME dup_skip backup [skip ci]" >>"$LOG_FILE" 2>&1 || true
            git push origin HEAD:main >>"$LOG_FILE" 2>&1 || true
        fi
        exit 0
    fi
    log_message "AVISO: No se encontro el archivo de hoy en GitHub. Procediendo como backup..."
fi

# --- Ejecución del Script de Python ---
log_message "Activando entorno virtual y ejecutando update_products.py..."
if [ -d "$VENV_PATH" ]; then
    source "$VENV_PATH/bin/activate"
fi

# Capturar stderr por separado para que tracebacks no se pierdan.
# Sin esto, si Python crashea con ImportError/etc, el log no muestra nada util.
UPDATE_STDERR_FILE=$(mktemp)
python3 "$SCRIPT_DIR/update_products.py" 2>"$UPDATE_STDERR_FILE"
PY_EXIT_CODE=$?
if [ -s "$UPDATE_STDERR_FILE" ]; then
    log_message "--- update_products stderr ---"
    cat "$UPDATE_STDERR_FILE" >>"$LOG_FILE"
    log_message "--- fin stderr ---"
fi
UPDATE_STDERR_SNIPPET=$(head -c 500 "$UPDATE_STDERR_FILE")
rm -f "$UPDATE_STDERR_FILE"

if [ $PY_EXIT_CODE -ne 0 ]; then
    if [ $PY_EXIT_CODE -eq 3 ]; then
        # supplier_down es una condicion ESPERADA y manejada: el proveedor no
        # respondio (timeout/500). El filler garantizado Lun-Sab cubre al
        # cliente igual. NO es CRITICO — reservamos esa palabra para fallos
        # inesperados (exit != 3). Ver CLAUDE.md Regla #2 (no sobre-alarmar).
        log_message "AVISO: proveedor no respondio (supplier_down, exit 3). El filler Lun-Sab cubre al cliente."
        pulse "supplier_down" "proveedor caido (timeout/500)"
        FAIL_REASON="supplier_down: API de Bertual caida o timeout. Exit=$PY_EXIT_CODE. Stderr: $UPDATE_STDERR_SNIPPET"
    else
        log_message "CRITICO: update_products fallo con codigo $PY_EXIT_CODE."
        pulse "supplier_fail" "update_products exit=$PY_EXIT_CODE"
        FAIL_REASON="supplier_fail: update_products exit=$PY_EXIT_CODE. Stderr: $UPDATE_STDERR_SNIPPET"
    fi
    # Igual corremos nightly_report: la garantia Lun-Sab mandara filler supplier_down.
    log_message "Corriendo nightly_report para filler 'supplier_down'..."
    python3 "$SCRIPT_DIR/nightly_report.py" >>"$LOG_FILE" 2>&1 || true
    # AIOps: diagnostico automatico del fallo con contexto completo
    log_message "Lanzando AIOps remediation con contexto del fallo..."
    python3 "$SCRIPT_DIR/aiops_remediate.py" "$FAIL_REASON" >>"$LOG_FILE" 2>&1 || true
    # Commit/push del heartbeat para trazabilidad
    git add status/heartbeat.json 2>/dev/null || true
    if ! git diff --cached --quiet 2>/dev/null; then
        git commit -m "chore: pulse $HOSTNAME fail-$PY_EXIT_CODE $TODAY_TAG [skip ci]" >>"$LOG_FILE" 2>&1 || true
        git push origin HEAD:main >>"$LOG_FILE" 2>&1 || true
    fi
    exit $PY_EXIT_CODE
fi

log_message "Sincronizando tenants (front + data mirror para testing)..."
python3 "$SCRIPT_DIR/sync_tenants.py" >>"$LOG_FILE" 2>&1 || log_message "ADVERTENCIA: sync_tenants fallo, continuando."

# Post-deploy check: verifica que cada sitio publico sirve los precios que la
# Pi acaba de generar. Si difieren, alerta Telegram al admin inmediatamente.
log_message "Post-deploy check: comparando webs publicas contra data local..."
if python3 "$SCRIPT_DIR/post_deploy_check.py" >>"$LOG_FILE" 2>&1; then
    log_message "Post-deploy OK: todos los sitios sirven la data del dia."
else
    log_message "ALERTA: post-deploy check fallo. Telegram enviado al admin. Revisar logs."
fi

log_message "Ejecutando reporte ejecutivo nocturno..."
python3 "$SCRIPT_DIR/nightly_report.py" >>"$LOG_FILE" 2>&1
NR_EXIT_CODE=$?
if [ $NR_EXIT_CODE -eq 0 ]; then
    log_message "Nightly OK (exit=0). Telegram deberia haber recibido el informe."
    pulse "updated" "update_products+nightly OK"
else
    log_message "ADVERTENCIA: nightly_report.py salio con codigo $NR_EXIT_CODE. Revisar status/metrics.jsonl."
    pulse "nightly_fail" "exit=$NR_EXIT_CODE"
fi

# --- No pushear si es un nodo de backup ---
if [ "$ROLE" = "backup" ]; then
    log_message "Nodo Secundario (Backup): No se realizará push a GitHub para evitar conflictos."
    # Backup igual pushea SU heartbeat para trazabilidad (no los .gz)
    git add status/heartbeat.json 2>/dev/null || true
    if ! git diff --cached --quiet 2>/dev/null; then
        git commit -m "chore: pulse $HOSTNAME backup updated $TODAY_TAG [skip ci]" >>"$LOG_FILE" 2>&1 || true
        git push origin HEAD:main >>"$LOG_FILE" 2>&1 || log_message "push de heartbeat fallo (backup)."
    fi
    exit 0
fi

# --- Gestión de Git (con Reintentos) ---
log_message "Procesando cambios en Git..."

# Solo si hay cambios reales para commitear
if [[ -n $(git status -s) ]]; then
    
    # SAFETY: stagear SOLO paths esperados, jamas `git add .` que podria
    # incluir .env.backup, dumps, credenciales que alguien dejo a mano.
    # Si .gitignore se rompe, este whitelist es la segunda red.
    git add \
        data/ \
        tenants/*/data/ \
        tenants/*/latest-json-filename.txt \
        tenants/*/latest-json-filename.json \
        latest-json-filename.txt \
        latest-json-filename.json \
        status/heartbeat.json \
        2>/dev/null || true

    # Tripwire pre-commit: si por error queda staged algo que parece secreto
    # (.env*, *secret*, *credential*, *.pem), abortar antes de commitear.
    if git diff --cached --name-only | grep -iE '(^|/)\.env|secret|credential|\.pem$|\.key$'; then
        log_message "ERROR: archivo sensible staged. Abortando commit. Revisar staging."
        git reset HEAD
        exit 1
    fi

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


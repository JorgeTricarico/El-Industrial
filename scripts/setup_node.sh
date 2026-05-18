#!/usr/bin/env bash
# Bootstrap reproducible de un nodo nuevo del cluster El-Industrial.
#
# Uso (en el dispositivo que vas a sumar):
#   git clone https://github.com/JorgeTricarico/El-Industrial.git
#   cd El-Industrial
#   ./scripts/setup_node.sh <role> <cron_offset_min>
#
#   role:             primary | backup | dev
#   cron_offset_min:  minutos despues de las 20:00 AR para correr el cron
#                     (ej: 0=20:00, 30=20:30, 60=21:00, 90=21:30). Coordinar
#                     con los nodos existentes para no solapar.
#
# Idempotente: podes correrlo varias veces sin romper nada. Si el venv ya
# existe lo reusa. Si el cron ya esta, lo reescribe con el offset nuevo.
#
# Lo que hace:
#   1. Crea venv en ./venv si no existe.
#   2. Instala requirements.txt en el venv.
#   3. Genera plantilla .env si no existe (vacia, con instrucciones).
#   4. Agrega entry al crontab del usuario actual (Lun-Sab).
#   5. Sugiere agregar el nodo a infra/nodes.yml.
#   6. Hace un pulse inicial para registrar el nodo en heartbeat.json.

set -euo pipefail

ROLE="${1:-backup}"
CRON_OFFSET="${2:-0}"
HOST="$(hostname)"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( dirname "$SCRIPT_DIR" )"
VENV="$PROJECT_ROOT/venv"

if ! [[ "$ROLE" =~ ^(primary|backup|dev)$ ]]; then
    echo "ERROR: role debe ser primary|backup|dev (recibido: $ROLE)" >&2
    exit 1
fi
if ! [[ "$CRON_OFFSET" =~ ^[0-9]+$ ]] || [ "$CRON_OFFSET" -gt 180 ]; then
    echo "ERROR: cron_offset_min debe ser numero 0..180 (recibido: $CRON_OFFSET)" >&2
    exit 1
fi

# Calculamos hora HH:MM AR a partir de 20:00 + offset
CRON_HOUR=$((20 + CRON_OFFSET / 60))
CRON_MIN=$((CRON_OFFSET % 60))
CRON_EXPR="${CRON_MIN} ${CRON_HOUR} * * 1-6"

echo "==================================="
echo "Bootstrap nodo: $HOST"
echo "Role:           $ROLE"
echo "Cron:           $CRON_EXPR  (Lun-Sab ${CRON_HOUR}:$(printf '%02d' $CRON_MIN) AR)"
echo "==================================="

# 1. Verificar python3
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 no instalado. Instalalo primero." >&2
    exit 1
fi

# 2. Crear venv si no existe
if [ ! -d "$VENV" ]; then
    echo "[1/5] Creando venv en $VENV ..."
    python3 -m venv "$VENV"
else
    echo "[1/5] venv ya existe en $VENV (reusando)"
fi

# 3. Instalar requirements
echo "[2/5] Instalando requirements.txt ..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$PROJECT_ROOT/requirements.txt"

# 4. .env plantilla si no existe
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo "[3/5] Generando .env plantilla (VACIO, completar a mano) ..."
    cat > "$PROJECT_ROOT/.env" <<'ENV_EOF'
# Credenciales del nodo. NO commitear (.gitignore lo cubre).
# Conseguir cada valor del .env del nodo primario (Pi) o de la persona a cargo.

# Bertual (proveedor mayorista)
BERTUAL_CUIT=
BERTUAL_PASSWORD=
BERTUAL_CLIENT_ID=

# Telegram bot (mismo bot para todos los nodos del cluster)
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=

# LLMs (cadena fallback Gemini -> Cerebras -> SambaNova)
GEMINI_API_KEY=
CEREBRAS_API_KEY=
SAMBANOVA_API_KEY=

# Netlify (solo si este nodo va a deployar)
NETLIFY_AUTH_TOKEN=
ENV_EOF
    chmod 600 "$PROJECT_ROOT/.env"
    echo "    -> $PROJECT_ROOT/.env (chmod 600). Completalo antes de que corra el cron."
else
    echo "[3/5] .env ya existe (no se toca)"
fi

# 5. Crontab entry
echo "[4/5] Actualizando crontab ..."
CRON_TAG="# el-industrial cluster node ($HOST)"
CRON_LINE="$CRON_EXPR $PROJECT_ROOT/scripts/run_daily.sh"

# Sacar entries viejas del cluster (por tag) y agregar la nueva
EXISTING=$(crontab -l 2>/dev/null | grep -v -F "$CRON_TAG" | grep -v -F "$PROJECT_ROOT/scripts/run_daily.sh" || true)
{
    [ -n "$EXISTING" ] && echo "$EXISTING"
    echo "$CRON_TAG"
    echo "$CRON_LINE"
} | crontab -

echo "    crontab actualizado:"
crontab -l | grep -A 1 "el-industrial cluster" || true

# 6. Pulso inicial
echo "[5/5] Pulso inicial al heartbeat ..."
"$VENV/bin/python" "$SCRIPT_DIR/node_pulse.py" --outcome "bootstrap" --note "setup_node.sh role=$ROLE offset=$CRON_OFFSET"

echo ""
echo "==================================="
echo "✓ Nodo $HOST bootstrapped."
echo ""
echo "PROXIMOS PASOS (a mano):"
echo "  1. Completar $PROJECT_ROOT/.env con las credenciales."
echo "  2. Sumar este nodo a infra/nodes.yml (estado: active) y commitear:"
echo ""
echo "       - hostname: $HOST"
echo "         role: $ROLE"
echo "         cron: \"$CRON_EXPR\""
echo "         location: \"<describir>\""
echo "         can_fetch_supplier: true"
echo "         can_push_github: true"
echo "         state: active"
echo ""
echo "  3. Probar a mano:"
echo "       $PROJECT_ROOT/scripts/run_daily.sh"
echo ""
echo "  4. system_audit confirmara el nodo en el cluster."
echo "==================================="

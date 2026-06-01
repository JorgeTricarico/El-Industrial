#!/bin/bash
# scripts/sync_env.sh — Sincroniza las credenciales (.env) desde WSL hacia la Pi y GitHub Secrets.
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( dirname "$SCRIPT_DIR" )"
ENV_FILE="$PROJECT_ROOT/.env"
PI_HOST="jorge@100.112.235.98"
PI_REPO_PATH="~/El-Industrial"

echo "=== Sincronizando Entorno y Credenciales ==="

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: No se encontro el archivo .env local en $ENV_FILE"
    exit 1
fi

# 1. Copiar .env a la Raspberry Pi
echo "→ Copiando .env a la Raspberry Pi ($PI_HOST)..."
if scp -q -o ConnectTimeout=10 "$ENV_FILE" "$PI_HOST:$PI_REPO_PATH/.env"; then
    echo "✓ .env copiado con exito a la Raspberry Pi."
else
    echo "⚠️ ADVERTENCIA: La Raspberry Pi no esta en linea o no se pudo conectar vía SSH."
fi

# 2. Sincronizar con GitHub Actions Secrets
if command -v gh &> /dev/null && gh auth status &>/dev/null; then
    echo "→ Sincronizando secretos con GitHub Actions..."
    # Leer el archivo .env y setear cada clave en GitHub Secrets (evitando comentarios y lineas vacias)
    while IFS= read -r line || [ -n "$line" ]; do
        # Omitir comentarios y lineas vacias
        [[ "$line" =~ ^# ]] && continue
        [[ -z "$line" ]] && continue
        
        # Extraer key y value
        if [[ "$line" =~ ^([A-Za-z0-9_]+)=(.*)$ ]]; then
            key="${BASH_REMATCH[1]}"
            val="${BASH_REMATCH[2]}"
            
            # Omitir variables locales no-sensibles si se desea
            if [[ "$key" =~ ^(BERTUAL_TIMEOUT|BERTUAL_RETRIES)$ ]]; then
                continue
            fi
            
            echo "  - Subiendo secreto: $key..."
            echo "$val" | gh secret set "$key" --repo "JorgeTricarico/El-Industrial"
        fi
    done < "$ENV_FILE"
    echo "✓ Secretos sincronizados con GitHub Actions."
else
    echo "⚠️ ADVERTENCIA: GitHub CLI ('gh') no esta autenticado o instalado. Omitiendo GitHub Secrets."
fi

echo "=== Sincronizacion Completada ==="

#!/bin/bash
# verify.sh — un solo comando para validar end-to-end despues de pushear.
#
# Reemplaza el round-trip manual: pull en Pi, refresh heartbeat, sync tenants,
# post-deploy check, smoke curl a cada Netlify URL. Se usa desde la laptop
# tras un `git push` para confirmar que la Pi corre lo nuevo y la red de
# seguridad sigue verde.
#
# Uso:
#   ./scripts/verify.sh             # full chain
#   ./scripts/verify.sh --fast      # skip post_deploy_check (sin Bertual)
#   ./scripts/verify.sh --local     # corre tests + audit local, no toca Pi
#
# Requiere: ssh a 100.112.235.98 sin password (key configurada), curl.

set -euo pipefail

PI_HOST="${PI_HOST:-jorge@100.112.235.98}"
PI_REPO="${PI_REPO:-~/El-Industrial}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; FAILS=$((FAILS+1)); }
info() { echo -e "${YELLOW}→${NC} $1"; }

FAILS=0
MODE="full"
case "${1:-}" in
  --fast)  MODE="fast" ;;
  --local) MODE="local" ;;
  -h|--help)
    sed -n '2,18p' "$0"; exit 0 ;;
  "") ;;
  *) echo "flag desconocido: $1 (usa --help)"; exit 2 ;;
esac

# ----- local checks (siempre corren) -----
info "tests locales"
(cd "$REPO_ROOT" && source venv/bin/activate 2>/dev/null || true; \
 python -m pytest tests/ -q 2>&1 | tail -3) && ok "pytest" || fail "pytest"

info "smoke audit local (sin enviar Telegram)"
(cd "$REPO_ROOT" && source venv/bin/activate 2>/dev/null || true; \
 TELEGRAM_TOKEN="" PYTHONPATH="$REPO_ROOT/scripts" python -c "
import system_audit
sec, total = system_audit.run_audit()
print(f'sections={len(sec)} problems={total}')
for s, p in sec.items():
    if p:
        print(f'  [{s}]')
        for x in p: print(f'    - {x}')
" 2>&1) && ok "system_audit run" || fail "system_audit run"

if [ "$MODE" = "local" ]; then
  echo ""
  if [ $FAILS -eq 0 ]; then ok "verify --local OK"; exit 0
  else fail "verify --local fallos: $FAILS"; exit 1; fi
fi

# ----- Pi: pull + heartbeat + sync_tenants -----
info "Pi: git pull --ff-only"
if ssh -o ConnectTimeout=10 "$PI_HOST" "cd $PI_REPO && git pull --ff-only 2>&1 | tail -3"; then
  ok "Pi pull"
else
  fail "Pi pull (Pi offline o conflicto?)"
  echo ""
  echo "Abortando: sin Pi no hay sentido seguir."
  exit 1
fi

info "Pi: refresh heartbeat"
ssh "$PI_HOST" "cd $PI_REPO && source venv/bin/activate && python scripts/refresh_heartbeat.py 2>&1 | tail -2" \
  && ok "heartbeat refresh" || fail "heartbeat refresh"

info "Pi: sync_tenants (espeja front + data, sin deploy a Netlify)"
ssh "$PI_HOST" "cd $PI_REPO && source venv/bin/activate && NETLIFY_AUTH_TOKEN='' python scripts/sync_tenants.py 2>&1 | tail -5" \
  && ok "sync_tenants" || fail "sync_tenants"

# ----- post_deploy_check (puede tardar: HTTP a Bertual + Netlify) -----
if [ "$MODE" = "full" ]; then
  info "Pi: post_deploy_check (data local Pi vs web publica vs Bertual API)"
  if ssh "$PI_HOST" "cd $PI_REPO && source venv/bin/activate && python scripts/post_deploy_check.py 2>&1 | tail -10"; then
    ok "post_deploy_check"
  else
    fail "post_deploy_check (revisar manual)"
  fi
fi

# ----- smoke curl a cada Netlify URL -----
info "Smoke curl a sites publicos"
SITES=$(python3 -c "
import yaml
with open('$REPO_ROOT/tenants/_registry.yml') as f:
    d = yaml.safe_load(f)
for t in d.get('tenants', []):
    if t.get('state') in ('active', 'testing'):
        print(f\"{t['slug']}|{t.get('netlify_url','')}\")")

while IFS='|' read -r slug url; do
  [ -z "$url" ] && continue
  code=$(curl -fsS -o /dev/null -w "%{http_code}" --max-time 15 "$url" || echo "000")
  if [ "$code" = "200" ]; then
    ok "$slug ($url) HTTP $code"
  else
    fail "$slug ($url) HTTP $code"
  fi
done <<< "$SITES"

# ----- resumen -----
echo ""
if [ $FAILS -eq 0 ]; then
  ok "verify END-TO-END OK"
  exit 0
else
  echo -e "${RED}verify fallo: $FAILS check(s) rojo(s)${NC}"
  exit 1
fi

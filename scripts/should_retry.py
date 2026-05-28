#!/usr/bin/env python3
"""Determina si algún tenant activo requiere reintento hoy por fallos del proveedor.

Retorna código de salida 0 si se necesita reintento (hubo un fallo y no se ha recuperado hoy),
o código de salida 1 si no se requiere reintento (todo OK, o no ha corrido aún el ciclo diario).
"""
import os
import sys
import json
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
STATUS_DIR = os.path.join(BASE_DIR, "status")
REGISTRY = os.path.join(BASE_DIR, "tenants", "_registry.yml")

def load_active_tenants():
    if not os.path.exists(REGISTRY):
        return []
    try:
        import yaml
        with open(REGISTRY, "r", encoding="utf-8") as f:
            tenants = (yaml.safe_load(f) or {}).get("tenants", [])
            return [t["slug"] for t in tenants if t.get("state") == "active"]
    except Exception:
        return []

def main():
    active_tenants = load_active_tenants()
    if not active_tenants:
        return 1  # Sin tenants activos, no hay nada que reintentar.

    # Obtener fecha de hoy en Argentina (zona horaria del cron)
    ar_tz = timezone(timedelta(hours=-3))
    today_str = datetime.now(ar_tz).strftime("%Y-%m-%d")

    metrics_path = os.path.join(STATUS_DIR, "metrics.jsonl")
    if not os.path.exists(metrics_path):
        return 1

    # Rastrear el último api status de hoy para cada tenant activo
    tenant_last_status = {}
    try:
        with open(metrics_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    ts = entry.get("ts", "")
                    # Convertir timestamp UTC/AR a fecha simple
                    if ts.startswith(today_str):
                        tenant = entry.get("tenant")
                        if tenant in active_tenants and "api" in entry:
                            tenant_last_status[tenant] = entry["api"]
                except Exception:
                    pass
    except Exception as e:
        print(f"[should_retry] Error leyendo metrics: {e}", file=sys.stderr)
        return 1

    # Evaluar si algún tenant activo quedó en estado fallido hoy
    retry_needed = False
    for tenant in active_tenants:
        last_status = tenant_last_status.get(tenant)
        if last_status in ("supplier_down", "api_fail"):
            print(f"[should_retry] Tenant '{tenant}' necesita reintento (último status hoy: {last_status})")
            retry_needed = True
        elif last_status == "ok":
            print(f"[should_retry] Tenant '{tenant}' está al día (status hoy: ok)")

    return 0 if retry_needed else 1

if __name__ == "__main__":
    sys.exit(main())

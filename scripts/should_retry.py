#!/usr/bin/env python3
"""Determina si algún tenant activo requiere reintento hoy por fallos del proveedor.

Retorna código de salida 0 si se necesita reintento (hubo un fallo y no se ha
recuperado hoy), o código de salida 1 si no se requiere reintento (todo OK,
o no ha corrido aún el ciclo diario).

Detección extendida (31-may-2026):
- Además de chequear api_fail/supplier_down en metrics.jsonl, ahora detecta
  data stale (el .gz más reciente tiene más de 26h) para capturar escenarios
  donde update_products nunca llegó a correr (ej: pull_fail) y por tanto nunca
  escribió ninguna entrada en metrics.
"""
import os
import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
STATUS_DIR = os.path.join(BASE_DIR, "status")
REGISTRY = os.path.join(BASE_DIR, "tenants", "_registry.yml")

STALE_HOURS = 26  # Si el .gz más reciente tiene más de esto, se considera stale.


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


def data_is_stale(tenant_slug: str):
    """Devuelve (is_stale, age_hours). Si no hay archivos, considera stale."""
    data_dir = Path(BASE_DIR) / "tenants" / tenant_slug / "data"
    if not data_dir.exists():
        return True, None
    gz_files = sorted(data_dir.glob("*.gz"))
    if not gz_files:
        return True, None
    latest = gz_files[-1]
    age_h = (datetime.now() - datetime.fromtimestamp(latest.stat().st_mtime)).total_seconds() / 3600
    return age_h > STALE_HOURS, round(age_h, 1)


def main():
    active_tenants = load_active_tenants()
    if not active_tenants:
        return 1  # Sin tenants activos, no hay nada que reintentar.

    # Obtener fecha de hoy en Argentina (zona horaria del cron)
    ar_tz = timezone(timedelta(hours=-3))
    today_str = datetime.now(ar_tz).strftime("%Y-%m-%d")

    metrics_path = os.path.join(STATUS_DIR, "metrics.jsonl")

    # Rastrear el último api status de hoy para cada tenant activo
    tenant_last_status = {}
    if os.path.exists(metrics_path):
        try:
            with open(metrics_path, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        ts = entry.get("ts", "")
                        if ts.startswith(today_str):
                            tenant = entry.get("tenant")
                            if tenant in active_tenants and "api" in entry:
                                tenant_last_status[tenant] = entry["api"]
                    except Exception:
                        pass
        except Exception as e:
            print(f"[should_retry] Error leyendo metrics: {e}", file=sys.stderr)

    # Evaluar si algún tenant activo quedó en estado fallido hoy
    retry_needed = False
    for tenant in active_tenants:
        last_status = tenant_last_status.get(tenant)

        # Check 1: api_fail o supplier_down en metrics de hoy
        if last_status in ("supplier_down", "api_fail"):
            print(f"[should_retry] '{tenant}' necesita reintento (metrics hoy: {last_status})")
            retry_needed = True
            continue

        if last_status == "ok":
            print(f"[should_retry] '{tenant}' OK en metrics de hoy.")
            continue

        # Check 2: si no hay entrada en metrics de hoy (ej: pull_fail abortó antes),
        # verificar la antigüedad del archivo .gz directamente.
        # Esto captura el escenario donde el cron murió antes de escribir a metrics.
        stale, age_h = data_is_stale(tenant)
        if stale:
            age_str = f"{age_h}h" if age_h is not None else "sin archivos"
            print(f"[should_retry] '{tenant}' data STALE ({age_str}, sin metrics hoy). Reintento necesario.")
            retry_needed = True
        else:
            print(f"[should_retry] '{tenant}' sin metrics hoy pero data fresca ({age_h}h). OK.")

    return 0 if retry_needed else 1


if __name__ == "__main__":
    sys.exit(main())

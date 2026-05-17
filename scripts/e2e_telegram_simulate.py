#!/usr/bin/env python3
"""E2E real: simula un cambio de precio y verifica entrega de Telegram.

Como funciona:
  1. Hace backup de tenants/<slug>/status/daily_accum.json (si existe).
  2. Escribe un accum sintetico con 1 update + 1 producto nuevo.
  3. Lee heartbeat.last_telegram_iso ANTES.
  4. Llama nightly_report.process_tenant_report con state='active' forzado.
  5. Lee heartbeat.last_telegram_iso DESPUES.
  6. Si bumped (delta > 0) Y sent=True -> PASS. Else FAIL.
  7. Restaura el accum original (o lo archiva si lo proceso archivo).

Default: demo-electricidad (clients.yml solo tiene a Jorge dev, no clientes reales).

EFECTOS REALES — manda Telegram de verdad. NO es no-op por default.
Por eso vive como script y no como pytest (los tests bloquean send_telegram).
Usar en la Pi: ssh jorge@100.112.235.98 -> source venv -> python scripts/e2e_telegram_simulate.py
"""
import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
TENANTS_DIR = os.path.join(BASE_DIR, "tenants")
STATUS_DIR = os.path.join(BASE_DIR, "status")

sys.path.insert(0, SCRIPT_DIR)
import heartbeat_io  # noqa: E402
import nightly_report as nr  # noqa: E402


SYNTHETIC_ACCUM = {
    "updated": {
        "E2E_TEST_001": {
            "code": "E2E_TEST_001",
            "name": "[E2E TEST] Producto sintetico — IGNORAR",
            "old": "100.00",
            "new": "115.00",
            "marca": "TEST_E2E",
        }
    },
    "new": {
        "E2E_TEST_NEW_001": {
            "code": "E2E_TEST_NEW_001",
            "name": "[E2E TEST] Producto nuevo sintetico — IGNORAR",
            "new": "999.00",
        }
    }
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="demo-electricidad",
                        help="slug a usar (default: demo-electricidad)")
    parser.add_argument("--dry-run", action="store_true",
                        help="prepara el accum y restaura, NO envia Telegram")
    args = parser.parse_args(argv)

    slug = args.tenant
    tenant_status = os.path.join(TENANTS_DIR, slug, "status")
    accum_path = os.path.join(tenant_status, "daily_accum.json")
    backup_path = accum_path + ".e2e_backup"

    if not os.path.isdir(os.path.join(TENANTS_DIR, slug)):
        print(f"❌ tenant {slug!r} no existe en {TENANTS_DIR}", file=sys.stderr)
        return 2

    # Step 1: backup accum existente (si hay)
    os.makedirs(tenant_status, exist_ok=True)
    had_existing = os.path.exists(accum_path)
    if had_existing:
        shutil.copy2(accum_path, backup_path)
        print(f"[1/6] backup hecho: {backup_path}")
    else:
        print(f"[1/6] no habia accum previo en {accum_path}")

    # Step 2: escribir accum sintetico
    with open(accum_path, "w", encoding="utf-8") as f:
        json.dump(SYNTHETIC_ACCUM, f, indent=2)
    print(f"[2/6] accum sintetico escrito ({len(SYNTHETIC_ACCUM['updated'])} update, {len(SYNTHETIC_ACCUM['new'])} new)")

    # Step 3: heartbeat ANTES
    hb_before = heartbeat_io.read(STATUS_DIR)
    tg_before = hb_before.get("last_telegram_iso", "")
    print(f"[3/6] heartbeat.last_telegram_iso ANTES: {tg_before or '(vacio)'}")

    if args.dry_run:
        print("[--dry-run] no se envia; restaurando estado.")
        _restore(accum_path, backup_path, had_existing)
        return 0

    # Step 4: trigger nightly_report (force state=active)
    print("[4/6] llamando nightly_report.process_tenant_report ...")
    fake_tenant = {"slug": slug, "state": "active"}
    t0 = time.time()
    try:
        result = nr.process_tenant_report(fake_tenant)
    except Exception as e:
        print(f"❌ excepcion en process_tenant_report: {type(e).__name__}: {e}", file=sys.stderr)
        _restore(accum_path, backup_path, had_existing)
        return 3
    elapsed = time.time() - t0
    print(f"      resultado: {result} ({elapsed:.1f}s)")

    # Step 5: heartbeat DESPUES
    hb_after = heartbeat_io.read(STATUS_DIR)
    tg_after = hb_after.get("last_telegram_iso", "")
    print(f"[5/6] heartbeat.last_telegram_iso DESPUES: {tg_after or '(vacio)'}")

    # Step 6: assert + cleanup
    # IMPORTANTE: nightly_report ARCHIVA el accum despues de mandarlo
    # (lo mueve a status/archive/), entonces el backup_path es la unica
    # forma de restaurar.
    _restore(accum_path, backup_path, had_existing)

    if not result.get("sent"):
        print(f"\n❌ FAIL: nightly_report devolvio sent=False (status={result.get('status')})", file=sys.stderr)
        return 1
    if tg_after == tg_before:
        print(f"\n❌ FAIL: heartbeat.last_telegram_iso no se actualizo (Telegram no entrego)", file=sys.stderr)
        return 1
    try:
        delta = (datetime.fromisoformat(tg_after) - datetime.fromisoformat(tg_before)).total_seconds() if tg_before else 999999
    except ValueError:
        delta = 999999
    if delta < 0:
        print(f"\n❌ FAIL: heartbeat fue hacia atras", file=sys.stderr)
        return 1

    print(f"\n[6/6] ✅ PASS: Telegram entrego (provider={result.get('provider')}, "
          f"items={result.get('items')}, delta_iso={int(delta)}s)")
    return 0


def _restore(accum_path, backup_path, had_existing):
    """Restaura el estado previo del accum del tenant.

    Hay 3 casos:
      (a) habia accum previo + backup -> mover backup a accum_path.
      (b) no habia accum + nightly_report lo archivo -> nada que hacer.
      (c) no habia accum + nightly_report NO lo archivo -> borrar el sintetico.
    """
    if os.path.exists(backup_path):
        # caso (a): restaurar
        shutil.move(backup_path, accum_path)
        print(f"      [restore] backup restaurado a {accum_path}")
        return
    if had_existing:
        # Backup no esta pero habia accum — algo raro. No tocamos para no perder data.
        print(f"      [restore] WARN: backup desaparecido, accum del tenant queda como esta", file=sys.stderr)
        return
    # caso (b) o (c)
    if os.path.exists(accum_path):
        os.remove(accum_path)
        print(f"      [restore] sintetico borrado")


if __name__ == "__main__":
    sys.exit(main())

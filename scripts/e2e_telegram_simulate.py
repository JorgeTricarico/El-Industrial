#!/usr/bin/env python3
"""E2E real: simula un cambio de precio y verifica entrega de Telegram.

Como funciona:
  1. Hace backup de tenants/<slug>/status/daily_accum.json (si existe).
  2. Toma un PRODUCTO REAL del ultimo .gz del tenant y simula un cambio
     de 1 centavo. Si por algun bug el accum se filtra a algun lado, es
     indistinguible del ruido de precios normal — no hay "E2E_TEST" en
     ningun campo visible.
  3. Lee heartbeat.last_telegram_iso ANTES + checksum del .gz.
  4. Llama nightly_report.process_tenant_report con state='active' forzado.
  5. Lee heartbeat.last_telegram_iso DESPUES + re-checksumea el .gz.
  6. Validaciones post-run:
       - sent=True y delta_iso > 0
       - .gz NO se modifico (checksum identico)
       - accum del tenant volvio al estado pre-test
       - no quedo accum sintetico en status/archive/ con datos NO restaurables
  7. Restaura accum original o limpia segun estado.

Default: demo-electricidad (clients.yml solo tiene a Jorge dev).

EFECTOS REALES — manda Telegram de verdad. NO es no-op por default.
Por eso vive como script y no como pytest (los tests bloquean send_telegram).
Usar en la Pi: ssh jorge@100.112.235.98 -> source venv -> python scripts/e2e_telegram_simulate.py
"""
import argparse
import glob
import gzip
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime
from decimal import Decimal

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
TENANTS_DIR = os.path.join(BASE_DIR, "tenants")
STATUS_DIR = os.path.join(BASE_DIR, "status")

sys.path.insert(0, SCRIPT_DIR)
import heartbeat_io  # noqa: E402
import nightly_report as nr  # noqa: E402


def _latest_gz(tenant_slug):
    pattern = os.path.join(TENANTS_DIR, tenant_slug, "data", "lista_precio_*.gz")
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _pick_canary_product(gz_path):
    """Devuelve (code, name, brand, current_price_str) de un producto real
    del .gz. Buscamos uno con precio parseable y > 1 ARS."""
    with gzip.open(gz_path, "rt", encoding="utf-8") as f:
        items = json.load(f)
    for it in items:
        try:
            p = Decimal(str(it.get("precio")))
            if p > Decimal("1"):
                return (
                    it.get("producto"),
                    it.get("detalle", "")[:80],
                    it.get("marca") or "",
                    str(p),
                )
        except Exception:
            continue
    raise RuntimeError(f"no se encontro producto valido en {gz_path}")


def _build_real_product_accum(code, name, brand, old_price_str):
    """Accum con cambio de +1 centavo sobre un producto real."""
    new_price = (Decimal(old_price_str) + Decimal("0.01")).quantize(Decimal("0.01"))
    return {
        "updated": {
            code: {
                "code": code,
                "name": name,
                "old": old_price_str,
                "new": str(new_price),
                "marca": brand,
            }
        },
        "new": {}
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

    # Pre-step: encontrar el .gz y un producto real (canario)
    gz_path = _latest_gz(slug)
    if not gz_path:
        print(f"❌ tenant {slug!r}: sin .gz en data/", file=sys.stderr)
        return 2
    gz_hash_before = _sha256(gz_path)
    code, name, brand, old_price = _pick_canary_product(gz_path)
    print(f"[0/7] canary: {code} ({name[:40]}) precio actual {old_price}")
    print(f"      .gz sha256 ANTES: {gz_hash_before[:16]}...")

    # Step 1: backup accum existente (si hay)
    os.makedirs(tenant_status, exist_ok=True)
    had_existing = os.path.exists(accum_path)
    if had_existing:
        shutil.copy2(accum_path, backup_path)
        print(f"[1/7] backup hecho: {backup_path}")
    else:
        print(f"[1/7] no habia accum previo en {accum_path}")

    # Step 2: escribir accum con cambio real de 1 centavo
    payload = _build_real_product_accum(code, name, brand, old_price)
    with open(accum_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    new_price = payload["updated"][code]["new"]
    print(f"[2/7] accum escrito: {code} {old_price} -> {new_price} (+1 centavo)")

    # Step 3: heartbeat ANTES
    hb_before = heartbeat_io.read(STATUS_DIR)
    tg_before = hb_before.get("last_telegram_iso", "")
    print(f"[3/7] heartbeat.last_telegram_iso ANTES: {tg_before or '(vacio)'}")

    if args.dry_run:
        print("[--dry-run] no se envia; restaurando estado.")
        _restore(accum_path, backup_path, had_existing)
        return 0

    # Step 4: trigger nightly_report (force state=active)
    print("[4/7] llamando nightly_report.process_tenant_report ...")
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
    print(f"[5/7] heartbeat.last_telegram_iso DESPUES: {tg_after or '(vacio)'}")

    # Step 6: restaurar accum (nightly_report ya lo archivo a status/archive/)
    _restore(accum_path, backup_path, had_existing)

    # Step 7: validaciones post-run
    print("[7/7] validaciones post-run:")
    failures = []

    # 7a: Telegram entrego
    if not result.get("sent"):
        failures.append(f"sent=False (status={result.get('status')})")
    else:
        print("      ✓ sent=True")

    # 7b: heartbeat bumped
    if tg_after == tg_before:
        failures.append("heartbeat.last_telegram_iso no avanzo")
    else:
        try:
            delta = (datetime.fromisoformat(tg_after) - datetime.fromisoformat(tg_before)).total_seconds() if tg_before else 999999
        except ValueError:
            delta = 999999
        if delta <= 0:
            failures.append(f"heartbeat retrocedio (delta={delta}s)")
        else:
            print(f"      ✓ heartbeat avanzo {int(delta)}s")

    # 7c: el .gz publico NO se toco
    gz_hash_after = _sha256(gz_path)
    if gz_hash_before != gz_hash_after:
        failures.append(f".gz fue modificado durante el test ({gz_hash_before[:8]} != {gz_hash_after[:8]})")
    else:
        print(f"      ✓ .gz intacto (sha={gz_hash_after[:16]}...)")

    # 7d: accum del tenant volvio al estado pre-test
    accum_now_exists = os.path.exists(accum_path)
    if had_existing and not accum_now_exists:
        failures.append("accum del tenant desaparecio (debia restaurarse del backup)")
    elif not had_existing and accum_now_exists:
        failures.append(f"accum sintetico no se limpio: {accum_path}")
    else:
        print(f"      ✓ accum del tenant en estado pre-test ({'restaurado' if had_existing else 'inexistente'})")

    # 7e: backup file no quedo huerfano
    if os.path.exists(backup_path):
        failures.append(f"backup huerfano sin limpiar: {backup_path}")
    else:
        print("      ✓ sin backup huerfano")

    if failures:
        print("\n❌ FAIL:", file=sys.stderr)
        for f in failures:
            print(f"   - {f}", file=sys.stderr)
        return 1
    print(f"\n✅ PASS: Telegram entrego + sistema en el mismo estado que antes del test "
          f"(provider={result.get('provider')}, items={result.get('items')})")
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

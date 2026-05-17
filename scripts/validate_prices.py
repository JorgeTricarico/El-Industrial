#!/usr/bin/env python3
"""Valida que el .gz local de un tenant matchea lo que devuelve su supplier.

Uso:
    validate_prices.py                 # primer tenant 'active' del registry
    validate_prices.py --tenant <slug>  # tenant especifico
    validate_prices.py --tenant <slug> ART001 ART002  # codigos a chequear

Multi-tenant aware desde 2026-05-17 (M1). Reemplaza el flujo viejo que leia
de data/ raiz + bertual_api hardcodeado.
"""
import argparse
import gzip
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
TENANTS_DIR = os.path.join(BASE_DIR, "tenants")
REGISTRY = os.path.join(TENANTS_DIR, "_registry.yml")

sys.path.insert(0, SCRIPT_DIR)
import suppliers  # noqa: E402
import update_products as up  # reusa load_registry, load_tenant_creds, load_tenant_config  # noqa: E402


def latest_gz(tenant_dir):
    data_dir = os.path.join(tenant_dir, "data")
    if not os.path.isdir(data_dir):
        return None
    gz = sorted([f for f in os.listdir(data_dir) if f.endswith(".gz")], reverse=True)
    return os.path.join(data_dir, gz[0]) if gz else None


def load_local(gz_path):
    with gzip.open(gz_path, "rt", encoding="utf-8") as f:
        return {p["producto"]: p for p in json.load(f)}


def pick_tenant(slug=None):
    tenants = up.load_registry()
    actives = [t for t in tenants if t.get("state") == "active"]
    if slug:
        for t in tenants:
            if t.get("slug") == slug:
                return t
        raise SystemExit(f"tenant '{slug}' no esta en _registry.yml")
    if not actives:
        raise SystemExit("no hay tenants active en _registry.yml")
    return actives[0]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", help="slug del tenant a validar (default: primer active)")
    parser.add_argument("codes", nargs="*", help="codigos a chequear (default: muestra 10 random del API)")
    args = parser.parse_args(argv)

    tenant = pick_tenant(args.tenant)
    slug = tenant["slug"]
    supplier_name = tenant.get("supplier")

    print(f"--- VALIDACIÓN DE INTEGRIDAD ({slug} / {supplier_name}) ---")

    try:
        supplier = suppliers.get(supplier_name)
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        return False

    creds = up.load_tenant_creds(slug)
    missing = [k for k in supplier.required_creds if not creds.get(k)]
    if missing:
        print(f"❌ faltan creds: {', '.join(missing)}", file=sys.stderr)
        return False

    api_products = supplier.fetch_products(creds)
    if not api_products:
        print("❌ supplier devolvio vacio", file=sys.stderr)
        return False

    gz_path = latest_gz(os.path.join(TENANTS_DIR, slug))
    if not gz_path:
        print(f"❌ no hay .gz en tenants/{slug}/data/", file=sys.stderr)
        return False
    local_data = load_local(gz_path)

    config = up.load_tenant_config(slug)
    # Transform via supplier para tener {producto, precio} consistente con lo escrito
    transformed = [supplier.transform_item(r, config) for r in api_products]
    api_map = {t["producto"]: t for t in transformed if t.get("producto")}

    to_check = args.codes if args.codes else list(api_map.keys())[:10]

    errors = 0
    checked = 0
    for code in to_check:
        if code in local_data and code in api_map:
            expected = api_map[code]["precio"]
            local_val = local_data[code]["precio"]
            checked += 1
            if str(local_val) != str(expected):
                print(f"❌ ERROR en {code}: API {expected} vs Local {local_val}")
                errors += 1
            else:
                print(f"✅ {code}: {local_val} verificado.")

    if checked == 0:
        print("⚠️ ningun codigo de la muestra coincidio entre API y local")
        return False
    return errors == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)

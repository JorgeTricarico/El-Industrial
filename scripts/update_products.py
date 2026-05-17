#!/usr/bin/env python3
"""Actualiza precios desde el supplier de cada tenant active.

Fase 2B (2026-05-17): itera tenants/_registry.yml en lugar de hardcodear
Bertual+root. Para cada tenant 'active':
  - Carga supplier adapter (Bertual/Haedo/...) desde scripts/suppliers/
  - Carga credenciales: tenants/<slug>/.env si existe, else .env raiz
  - Carga config del tenant: tenants/<slug>/config/config.json (markup/iva)
  - Fetcha productos, calcula precios, diff vs ultimo .gz local del tenant
  - Escribe tenants/<slug>/data/lista_precio_*.gz + latest-json-filename.txt
  - Acumula cambios en tenants/<slug>/status/daily_accum.json

Backward compat: para el tenant 'el-industrial' tambien escribe a data/
raiz + status/ raiz porque hay scripts/validate_prices.py y analyze que
todavia leen de ahi. Eso se ira sacando cuando esos scripts se migren.

Flags:
  --silent   no escribe gz a disco (solo procesa diff y acumula)
  --report   se preserva por compat con scripts viejos (no-op aca)
"""
import gzip
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime

import requests  # noqa: F401  (usado por adapters)
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
TENANTS_DIR = os.path.join(BASE_DIR, "tenants")
REGISTRY = os.path.join(TENANTS_DIR, "_registry.yml")

# Root paths — compat para tenant primario (el-industrial). Cuando se migren
# los consumers (validate_prices, analyze_prices) este bloque se va.
LATEST_INDEX_FILE = os.path.join(BASE_DIR, "latest-json-filename.txt")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
DATA_DIR = os.path.join(BASE_DIR, "data")
STATUS_DIR = os.path.join(BASE_DIR, "status")
ENV_FILE = os.path.join(BASE_DIR, ".env")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# Tenant cuyo data/ se mirrorea al root por compat. None = no mirror.
PRIMARY_TENANT_SLUG = "el-industrial"

load_dotenv(ENV_FILE)

sys.path.insert(0, SCRIPT_DIR)
import suppliers  # noqa: E402


# -------- utilidades comunes (heartbeat, metrics, git) --------

def _git_head_short():
    try:
        out = subprocess.check_output(
            ["git", "-C", BASE_DIR, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unknown"


def update_heartbeat(host, status="ok", duration_s=0):
    """Heartbeat global (no por tenant). Sigue en root status/."""
    os.makedirs(STATUS_DIR, exist_ok=True)
    payload = {
        "last_run": datetime.now().isoformat(),
        "node": host,
        "status": status,
        "duration_s": round(duration_s, 2),
        "version": _git_head_short(),
    }
    try:
        with open(os.path.join(STATUS_DIR, "heartbeat.json"), "w") as f:
            json.dump(payload, f, indent=2)
    except OSError as e:
        print(f"[heartbeat] error escribiendo: {e}", file=sys.stderr)


def log_metrics(host, api_status, updates=0, peer_status="unknown",
                start_ts=None, tenant_slug=None):
    os.makedirs(STATUS_DIR, exist_ok=True)
    duration = round(time.time() - start_ts, 2) if start_ts else 0
    entry = {
        "ts": datetime.now().isoformat(),
        "node": host,
        "api": api_status,
        "duration": duration,
        "updates": updates,
        "peer": peer_status,
    }
    if tenant_slug:
        entry["tenant"] = tenant_slug
    try:
        with open(os.path.join(STATUS_DIR, "metrics.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        print(f"[metrics] error: {e}", file=sys.stderr)


def check_node_status(ip):
    try:
        subprocess.check_output(["ping", "-c", "1", "-W", "1", ip], stderr=subprocess.DEVNULL)
        return "online"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "offline"


def fetch_with_retries(supplier, creds):
    """Llama supplier.fetch_products(creds) con backoff exponencial."""
    last_err = None
    for i in range(3):
        try:
            t0 = time.time()
            data = supplier.fetch_products(creds)
            if data and len(data) > 100:
                return data, round(time.time() - t0, 2)
            last_err = f"respuesta corta o vacia (len={len(data) if data else 0})"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(2 ** i)
    print(f"[{supplier.name}] agotados 3 intentos: {last_err}", file=sys.stderr)
    return None, 0


# -------- helpers per-tenant --------

def load_registry():
    if not os.path.exists(REGISTRY):
        return []
    try:
        import yaml
    except ImportError:
        return []
    try:
        with open(REGISTRY, "r", encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("tenants", [])
    except (OSError, Exception):
        return []


def load_tenant_creds(slug):
    """Lee tenants/<slug>/.env si existe, else fallback a .env raiz.
    Devuelve dict de TODAS las claves de env actuales (raiz mergeada con
    overrides del tenant). El supplier toma lo que necesita.
    """
    out = dict(os.environ)  # raiz ya esta cargado al import time
    tenant_env = os.path.join(TENANTS_DIR, slug, ".env")
    if os.path.exists(tenant_env):
        try:
            with open(tenant_env, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    out[k.strip()] = v.strip().strip('"').strip("'")
        except OSError as e:
            print(f"[{slug}] error leyendo .env tenant: {e}", file=sys.stderr)
    return out


def load_tenant_config(slug):
    """Lee tenants/<slug>/config/config.json. Si falta, defaults seguros."""
    path = os.path.join(TENANTS_DIR, slug, "config", "config.json")
    if not os.path.exists(path):
        return {"markup": 0.0, "iva": 0.0}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"markup": 0.0, "iva": 0.0}


def tenant_data_dir(slug):
    return os.path.join(TENANTS_DIR, slug, "data")


def tenant_status_dir(slug):
    return os.path.join(TENANTS_DIR, slug, "status")


def read_old_items(tenant_dir):
    """Lee el ultimo .gz del tenant. Devuelve dict {producto: item} o {}."""
    data_dir = os.path.join(tenant_dir, "data")
    if not os.path.isdir(data_dir):
        return {}
    gz_files = sorted([f for f in os.listdir(data_dir) if f.endswith(".gz")], reverse=True)
    if not gz_files:
        return {}
    try:
        with gzip.open(os.path.join(data_dir, gz_files[0]), "rt", encoding="utf-8") as gf:
            return {p["producto"]: p for p in json.load(gf)}
    except (OSError, json.JSONDecodeError, gzip.BadGzipFile) as e:
        print(f"[old_data] {gz_files[0]}: {e}", file=sys.stderr)
        return {}


def diff_items(new_items, old_data):
    changes = {"updated": [], "new": []}
    for item in new_items:
        c = item["producto"]
        p = item["precio"]
        if c in old_data and old_data[c]["precio"] != p:
            changes["updated"].append({
                "code": c, "name": item["detalle"],
                "old": old_data[c]["precio"], "new": p,
            })
        elif c not in old_data:
            changes["new"].append({"code": c, "name": item["detalle"], "new": p})
    return changes


def update_accumulator(changes, accum_dir):
    """Mergea changes en daily_accum.json del directorio dado."""
    os.makedirs(accum_dir, exist_ok=True)
    accum_path = os.path.join(accum_dir, "daily_accum.json")
    accum = {"updated": {}, "new": {}}
    if os.path.exists(accum_path):
        try:
            with open(accum_path, "r") as f:
                accum = json.load(f)
                if not isinstance(accum, dict) or "new" not in accum:
                    raise ValueError("estructura invalida")
        except (json.JSONDecodeError, OSError, ValueError) as e:
            print(f"[accum] reset por error: {e}", file=sys.stderr)
            accum = {"updated": {}, "new": {}}

    for item in changes.get("new", []):
        accum["new"][item["code"]] = item
    for item in changes.get("updated", []):
        if item["code"] in accum["new"]:
            accum["new"][item["code"]]["new"] = item["new"]
        else:
            if item["code"] in accum["updated"]:
                accum["updated"][item["code"]]["new"] = item["new"]
            else:
                accum["updated"][item["code"]] = item

    try:
        with open(accum_path, "w") as f:
            json.dump(accum, f, indent=2)
    except OSError as e:
        print(f"[accum] error escribiendo: {e}", file=sys.stderr)


def write_tenant_dataset(slug, new_items, silent=False):
    """Escribe data/<gz> + latest-json-filename.{txt,json} dentro de
    tenants/<slug>/. Si silent=True solo no escribe."""
    if silent:
        return None, None
    tdir = os.path.join(TENANTS_DIR, slug)
    data_dir = os.path.join(tdir, "data")
    os.makedirs(data_dir, exist_ok=True)
    filename = f"lista_precio_{datetime.now().strftime('%y-%m-%d')}_json_compres.gz"
    rel_path = os.path.join("data", filename)
    abs_path = os.path.join(tdir, rel_path)
    with gzip.open(abs_path, "wt", encoding="utf-8") as f:
        json.dump(new_items, f, indent=2, ensure_ascii=False)
    # Pointer plano (compat con el front)
    with open(os.path.join(tdir, "latest-json-filename.txt"), "w") as f:
        f.write(rel_path)
    # Pointer json (compat con el front)
    with open(os.path.join(tdir, "latest-json-filename.json"), "w") as f:
        json.dump({"filename": rel_path}, f)
    return rel_path, abs_path


def write_root_compat(new_items, silent=False):
    """Mirror al root data/ + latest-json-filename para tools legacy.
    Solo para el tenant primario.
    """
    if silent:
        return
    os.makedirs(DATA_DIR, exist_ok=True)
    filename = f"lista_precio_{datetime.now().strftime('%y-%m-%d')}_json_compres.gz"
    rel_path = os.path.join("data", filename)
    abs_path = os.path.join(BASE_DIR, rel_path)
    with gzip.open(abs_path, "wt", encoding="utf-8") as f:
        json.dump(new_items, f, indent=2, ensure_ascii=False)
    with open(LATEST_INDEX_FILE, "w") as f:
        f.write(rel_path)


# -------- core: process_tenant --------

def process_tenant(tenant, silent=False):
    """Procesa un tenant: fetch, diff, acumula, escribe. Retorna dict
    con info para el caller (cantidad de cambios, status, etc).
    """
    slug = tenant.get("slug")
    supplier_name = tenant.get("supplier")
    state = tenant.get("state", "inactive")

    result = {
        "slug": slug, "supplier": supplier_name, "state": state,
        "status": "skip", "updates": 0, "new": 0, "error": None,
    }

    if state != "active":
        result["status"] = f"skip_state_{state}"
        return result

    try:
        supplier = suppliers.get(supplier_name)
    except ValueError as e:
        result["status"] = "supplier_unknown"
        result["error"] = str(e)
        return result

    creds = load_tenant_creds(slug)
    missing = [k for k in supplier.required_creds if not creds.get(k)]
    if missing:
        result["status"] = "creds_missing"
        result["error"] = f"falta {', '.join(missing)}"
        return result

    config = load_tenant_config(slug)

    raw_data, _ = fetch_with_retries(supplier, creds)
    if not raw_data:
        result["status"] = "api_fail"
        return result

    tenant_dir = os.path.join(TENANTS_DIR, slug)
    new_items = [supplier.transform_item(r, config) for r in raw_data]
    new_items = [i for i in new_items if i.get("producto")]  # descarta items sin codigo

    old_data = read_old_items(tenant_dir)
    changes = diff_items(new_items, old_data)

    # accum per-tenant
    update_accumulator(changes, tenant_status_dir(slug))

    # Para el tenant primario, mantener mirror al root status/daily_accum.json
    # asi nightly_report (en modo legacy) sigue encontrando data hasta que
    # se migre.
    if slug == PRIMARY_TENANT_SLUG:
        update_accumulator(changes, STATUS_DIR)

    rel, _ = write_tenant_dataset(slug, new_items, silent=silent)
    if slug == PRIMARY_TENANT_SLUG:
        write_root_compat(new_items, silent=silent)

    result["status"] = "ok"
    result["updates"] = len(changes["updated"])
    result["new"] = len(changes["new"])
    result["filename"] = rel
    return result


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    silent = "--silent" in argv

    host = socket.gethostname()
    start_ts = time.time()
    peer_status = check_node_status("100.115.152.45")

    tenants = load_registry()
    if not tenants:
        print("[update_products] _registry.yml vacio o ausente", file=sys.stderr)
        update_heartbeat(host, status="no_tenants", duration_s=time.time() - start_ts)
        return 1

    any_ok = False
    any_fail = False
    for t in tenants:
        res = process_tenant(t, silent=silent)
        status = res["status"]
        if status == "ok":
            print(f"[{res['slug']}] OK: updates={res['updates']} new={res['new']}")
            log_metrics(host, "ok", res["updates"] + res["new"], peer_status,
                        start_ts, tenant_slug=res["slug"])
            any_ok = True
        elif status.startswith("skip"):
            # tenants en testing/inactive no son fallo
            print(f"[{res['slug']}] {status}")
        else:
            print(f"[{res['slug']}] FAIL ({status}): {res.get('error', '')}", file=sys.stderr)
            log_metrics(host, status, 0, peer_status, start_ts, tenant_slug=res["slug"])
            any_fail = True

    duration = time.time() - start_ts
    if any_ok and not any_fail:
        update_heartbeat(host, status="ok", duration_s=duration)
        return 0
    elif any_ok and any_fail:
        update_heartbeat(host, status="partial_fail", duration_s=duration)
        return 0  # mantenemos exit 0 si al menos un tenant succeeded
    else:
        update_heartbeat(host, status="api_fail", duration_s=duration)
        return 1


if __name__ == "__main__":
    sys.exit(main())

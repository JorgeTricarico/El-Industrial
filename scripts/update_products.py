#!/usr/bin/env python3
import json, gzip, os, time, subprocess, sys, glob
from datetime import datetime
import requests, xlsxwriter
from dotenv import load_dotenv

try:
    from bertual_api import BertualAPIClient
except ImportError:
    import sys
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from bertual_api import BertualAPIClient

# --- Rutas Dinámicas ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
LATEST_INDEX_FILE = os.path.join(BASE_DIR, "latest-json-filename.txt")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
DATA_DIR = os.path.join(BASE_DIR, "data")
STATUS_DIR = os.path.join(BASE_DIR, "status")
ENV_FILE = os.path.join(BASE_DIR, ".env")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

load_dotenv(ENV_FILE)
def load_config():
    if not os.path.exists(CONFIG_FILE): return {"markup": 0.0, "iva": 0.0}
    try:
        with open(CONFIG_FILE, "r") as f: return json.load(f)
    except: return {"markup": 0.0, "iva": 0.0}

config = load_config()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

def get_daily_accum_path():
    return os.path.join(STATUS_DIR, "daily_accum.json")

def _git_head_short():
    """Devuelve el SHA corto del HEAD actual. Si falla, retorna 'unknown'."""
    try:
        out = subprocess.check_output(
            ["git", "-C", BASE_DIR, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unknown"


def update_heartbeat(host, status="ok", duration_s=0):
    """Heartbeat con status enriquecido. Se actualiza tanto en exito como en fallo.

    Incluye el SHA corto del HEAD para verificar que cada nodo pulleo la
    ultima version del codigo antes de correr.
    """
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

def update_accumulator(changes):
    os.makedirs(STATUS_DIR, exist_ok=True)
    accum_path = get_daily_accum_path()
    accum = {"updated": {}, "new": {}}
    
    if os.path.exists(accum_path):
        try:
            with open(accum_path, "r") as f:
                accum = json.load(f)
                if not isinstance(accum, dict) or "new" not in accum:
                    raise ValueError("daily_accum.json con estructura invalida")
        except (json.JSONDecodeError, OSError, ValueError) as e:
            print(f"[accum] reset por error: {e}", file=sys.stderr)
            accum = {"updated": {}, "new": {}}
    
    for item in changes.get("new", []): accum["new"][item["code"]] = item
    for item in changes.get("updated", []):
        if item["code"] in accum["new"]: accum["new"][item["code"]]["new"] = item["new"]
        else:
            if item["code"] in accum["updated"]: accum["updated"][item["code"]]["new"] = item["new"]
            else: accum["updated"][item["code"]] = item
            
    try:
        with open(accum_path, "w") as f:
            json.dump(accum, f, indent=2)
    except OSError as e:
        print(f"[accum] error escribiendo: {e}", file=sys.stderr)

def transform_item(i):
    neto = i.get("Precio", 0)
    p = neto * (1 + config.get("iva", 0)) * (1 + config.get("markup", 0))
    c = i.get("Articulo_Corto") or i.get("Articulo")
    m_raw = str(i.get("Moneda", "")).strip().upper()
    
    if m_raw in ["PES", "ARS"]: m = "$"
    elif m_raw in ["DOL", "USD"]: m = "U$S"
    else: m = m_raw # Mantener EUR, etc.
    
    return {"producto": c, "detalle": i.get("Descripcion"), "marca": i.get("Familia", "").strip(), "moneda": m, "precio": "{:.2f}".format(p)}

def log_metrics(host, api_status, updates=0, peer_status="unknown", start_ts=None, changes=None):
    os.makedirs(STATUS_DIR, exist_ok=True)
    duration = round(time.time() - start_ts, 2) if start_ts else 0
    entry = {"ts": datetime.now().isoformat(), "node": host, "api": api_status, "duration": duration, "updates": updates, "peer": peer_status}
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

def fetch_with_retries(client):
    last_err = None
    for i in range(3):
        try:
            t0 = time.time()
            data = client.fetch_products()
            if data and len(data) > 100:
                return data, round(time.time()-t0, 2)
            last_err = f"respuesta corta o vacia (len={len(data) if data else 0})"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(2 ** i)  # backoff 1s, 2s, 4s
    print(f"[bertual] agotados 3 intentos: {last_err}", file=sys.stderr)
    return None, 0

def calculate_price(neto):
    return neto * (1 + config.get("iva", 0)) * (1 + config.get("markup", 0))

if __name__ == "__main__":
    is_report_run = "--report" in sys.argv
    import socket; host = socket.gethostname(); start_ts = time.time()
    peer_status = check_node_status("100.115.152.45")
    
    api_data, api_lat = fetch_with_retries(BertualAPIClient())
    if not api_data:
        update_heartbeat(host, status="api_fail", duration_s=time.time() - start_ts)
        log_metrics(host, "api_fail", 0, peer_status, start_ts)
        exit(1)

    try:
        with open(LATEST_INDEX_FILE, "r") as f:
            with gzip.open(os.path.join(BASE_DIR, f.read().strip()), "rt") as gf:
                old_data = {p["producto"]: p for p in json.load(gf)}
    except (OSError, json.JSONDecodeError, gzip.BadGzipFile) as e:
        print(f"[old_data] no se pudo leer la lista previa (esperado en primera corrida): {e}", file=sys.stderr)
        old_data = {}

    new_items = [transform_item(i) for i in api_data]
    changes = {"updated": [], "new": []}
    for item in new_items:
        c = item["producto"]; p = item["precio"]
        if c in old_data and old_data[c]["precio"] != p:
            changes["updated"].append({"code": c, "name": item["detalle"], "old": old_data[c]["precio"], "new": p})
        elif c not in old_data:
            changes["new"].append({"code": c, "name": item["detalle"], "new": p})
            
    update_accumulator(changes)
    log_metrics(host, "ok", len(changes["updated"]) + len(changes["new"]), peer_status, start_ts, changes)
    update_heartbeat(host, status="ok", duration_s=time.time() - start_ts)
    
    filename = f"lista_precio_{datetime.now().strftime('%y-%m-%d')}_json_compres.gz"
    rel_path = os.path.join("data", filename)
    
    if "--silent" not in sys.argv:
        with gzip.open(os.path.join(BASE_DIR, rel_path), "wt", encoding="utf-8") as f: 
            json.dump(new_items, f, indent=2, ensure_ascii=False)
        with open(LATEST_INDEX_FILE, "w") as f: f.write(rel_path)


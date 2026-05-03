#!/usr/bin/env python3
import json, gzip, os, sys
from bertual_api import BertualAPIClient

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LATEST_INDEX_FILE = os.path.join(BASE_DIR, "latest-json-filename.txt")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

def load_config():
    with open(CONFIG_FILE, "r") as f: return json.load(f)

def get_latest_local_data():
    with open(LATEST_INDEX_FILE, "r") as f: rel_path = f.read().strip()
    full_path = os.path.join(BASE_DIR, rel_path)
    with gzip.open(full_path, "rt", encoding="utf-8") as f:
        return {p["producto"]: p for p in json.load(f)}

def main(check_codes=None):
    config = load_config()
    client = BertualAPIClient()
    api_products = client.fetch_products()
    if not api_products: return False
    
    local_data = get_latest_local_data()
    errors = 0
    
    # Si nos pasan codigos especificos (los que cambiaron), probamos esos. 
    # Si no, probamos una muestra aleatoria.
    to_check = check_codes if check_codes else [p.get("Articulo_Corto") or p.get("Articulo") for p in api_products[:10]]
    
    api_map = { (p.get("Articulo_Corto") or p.get("Articulo")): p for p in api_products }

    print(f"--- VALIDACIÓN DE INTEGRIDAD ---")
    for code in to_check:
        if code in local_data and code in api_map:
            prod = api_map[code]
            api_neto = float(prod.get("Precio") or prod.get("Precio_Neto") or 0)
            expected = "{:.2f}".format(api_neto * (1 + config['iva']) * (1 + config['markup']))
            local_val = local_data[code]["precio"]
            
            if local_val != expected:
                print(f"❌ ERROR en {code}: API {expected} vs Local {local_val}")
                errors += 1
            else:
                print(f"✅ {code}: {local_val} verificado.")

    return errors == 0

if __name__ == "__main__":
    codes = sys.argv[1:] if len(sys.argv) > 1 else None
    success = main(codes)
    sys.exit(0 if success else 1)

#!/usr/bin/env python3
"""Sincronizacion de tenants:
- Copia el front compartido (index.html, style.css, js/) de la raiz a cada tenant.
- Para tenants en estado 'testing': clona el ultimo .gz de data/ raiz al tenant
  para que el Netlify de prueba se vea actualizado sin tener API Bertual propia.
- Para tenants 'active' con bertual_account propia: NO toca su data
  (su update_products se encarga).

Se llama desde run_daily.sh al final del flow para que cada cron actualice
todos los Netlify sites con el ultimo HEAD del repo.
"""
import io
import json
import os
import shutil
import sys
import zipfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
TENANTS_DIR = os.path.join(BASE_DIR, "tenants")
REGISTRY = os.path.join(TENANTS_DIR, "_registry.yml")

# Archivos del front que se replican a cada tenant
FRONT_FILES = ["index.html", "style.css", "tornillo.png"]
FRONT_DIRS = ["js"]


def load_registry():
    if not os.path.exists(REGISTRY):
        return []
    try:
        import yaml
    except ImportError:
        print("[sync_tenants] pyyaml no instalado", file=sys.stderr)
        return []
    with open(REGISTRY, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("tenants", [])


def copy_front(tenant_dir):
    """Copia archivos compartidos del front a tenants/<slug>/."""
    copied = 0
    for fname in FRONT_FILES:
        src = os.path.join(BASE_DIR, fname)
        if not os.path.exists(src):
            continue
        dst = os.path.join(tenant_dir, fname)
        # Solo copiar si difiere (evita commits innecesarios)
        if not os.path.exists(dst) or _file_differs(src, dst):
            shutil.copy2(src, dst)
            copied += 1
    for dname in FRONT_DIRS:
        src = os.path.join(BASE_DIR, dname)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(tenant_dir, dname)
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        # Excluir tests *.test.js del front del tenant
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.test.js"))
        copied += 1
    return copied


def _file_differs(a, b):
    try:
        return open(a, "rb").read() != open(b, "rb").read()
    except OSError:
        return True


def mirror_data_to_testing_tenant(tenant_slug, tenant_dir):
    """Copia el .gz mas reciente de data/ raiz al tenant en estado testing."""
    src_data = os.path.join(BASE_DIR, "data")
    if not os.path.isdir(src_data):
        return False
    gz_files = sorted(
        [f for f in os.listdir(src_data) if f.endswith(".gz")],
        reverse=True,
    )
    if not gz_files:
        return False
    latest = gz_files[0]

    dst_data = os.path.join(tenant_dir, "data")
    os.makedirs(dst_data, exist_ok=True)
    src = os.path.join(src_data, latest)
    dst = os.path.join(dst_data, latest)
    if not os.path.exists(dst) or _file_differs(src, dst):
        shutil.copy2(src, dst)

    # Actualizar pointers
    pointer_txt = os.path.join(tenant_dir, "latest-json-filename.txt")
    with open(pointer_txt, "w", encoding="utf-8") as f:
        f.write("data/" + latest + "\n")
    pointer_json = os.path.join(tenant_dir, "latest-json-filename.json")
    with open(pointer_json, "w", encoding="utf-8") as f:
        json.dump({"filename": "data/" + latest}, f)

    # Borrar .gz viejos del tenant (mantener solo el ultimo)
    for f in os.listdir(dst_data):
        if f.endswith(".gz") and f != latest:
            try:
                os.remove(os.path.join(dst_data, f))
            except OSError:
                pass
    return True


def deploy_to_netlify(tenant_dir, site_id, token):
    """Sube el contenido de tenant_dir como un nuevo deploy a Netlify.
    Retorna (ok, message). Silently skip si falta token o site_id.
    """
    if not token or not site_id:
        return (False, "sin token o site_id")
    try:
        import requests
    except ImportError:
        return (False, "requests no instalado")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(tenant_dir):
            for f in files:
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, tenant_dir))
    buf.seek(0)

    url = f"https://api.netlify.com/api/v1/sites/{site_id}/deploys"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/zip"}
    try:
        res = requests.post(url, headers=headers, data=buf.read(), timeout=120)
    except requests.RequestException as e:
        return (False, f"{type(e).__name__}: {e}")

    if not res.ok:
        return (False, f"HTTP {res.status_code}: {res.text[:200]}")
    body = res.json()
    return (True, f"deploy_id={body.get('id')} state={body.get('state')}")


def main():
    tenants = load_registry()
    if not tenants:
        print("[sync_tenants] No hay tenants en _registry.yml")
        return 0

    netlify_token = os.environ.get("NETLIFY_AUTH_TOKEN", "")

    for t in tenants:
        slug = t.get("slug")
        state = t.get("state", "inactive")
        if not slug or state == "inactive":
            continue
        tenant_dir = os.path.join(TENANTS_DIR, slug)
        if not os.path.isdir(tenant_dir):
            print(f"[sync_tenants] {slug}: carpeta {tenant_dir} no existe, skip")
            continue

        copied = copy_front(tenant_dir)
        print(f"[sync_tenants] {slug}: front sincronizado ({copied} items)")

        if state == "testing":
            ok = mirror_data_to_testing_tenant(slug, tenant_dir)
            print(f"[sync_tenants] {slug}: data mirror = {ok}")

        site_id = t.get("netlify_site_id")
        if site_id and netlify_token:
            ok, msg = deploy_to_netlify(tenant_dir, site_id, netlify_token)
            tag = "OK" if ok else "FAIL"
            print(f"[sync_tenants] {slug}: netlify deploy {tag} - {msg}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

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

# Archivos del front compartidos que se replican a cada tenant.
# El logo NO va aca: cada tenant aporta el suyo (apuntado por branding.logoUrl).
FRONT_FILES = ["index.html", "style.css"]
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


def mirror_data_to_tenant(tenant_slug, tenant_dir):
    """Copia el .gz mas reciente de data/ raiz al tenant — SOLO si el tenant
    no tiene uno mas fresco.

    Post-M1 (2026-05-17): update_products escribe directo a tenants/<slug>/data/.
    Esta funcion existia para legacy 'testing' que vivian del root. Pero si
    update_products ya escribio al tenant con fecha >= root, NO debemos copiar
    porque sobreescribimos data buena con vieja.

    Regla: comparar el nombre del .gz mas reciente del tenant vs root.
    - Si tenant >= root: no tocar nada (tenant ya tiene data fresca o igual).
    - Si root > tenant: copiar (legacy testing).
    - Si tenant no tiene .gz: copiar (bootstrap inicial).
    """
    src_data = os.path.join(BASE_DIR, "data")
    if not os.path.isdir(src_data):
        return False
    root_gz = sorted(
        [f for f in os.listdir(src_data) if f.endswith(".gz")],
        reverse=True,
    )
    if not root_gz:
        return False
    latest_root = root_gz[0]

    dst_data = os.path.join(tenant_dir, "data")
    os.makedirs(dst_data, exist_ok=True)
    tenant_gz = sorted(
        [f for f in os.listdir(dst_data) if f.endswith(".gz")],
        reverse=True,
    )
    latest_tenant = tenant_gz[0] if tenant_gz else ""

    # Comparacion por nombre de archivo (lista_precio_YY-MM-DD_...). Como YY-MM-DD
    # es lexicograficamente ordenable, el max() string es el mas reciente.
    if latest_tenant >= latest_root and latest_tenant:
        # Tenant tiene >= que root. No tocar. Solo asegurar que el pointer
        # apunta al .gz del tenant.
        pointer_txt = os.path.join(tenant_dir, "latest-json-filename.txt")
        with open(pointer_txt, "w", encoding="utf-8") as f:
            f.write("data/" + latest_tenant + "\n")
        pointer_json = os.path.join(tenant_dir, "latest-json-filename.json")
        with open(pointer_json, "w", encoding="utf-8") as f:
            json.dump({"filename": "data/" + latest_tenant}, f)
        return True

    # root > tenant -> legacy copy.
    src = os.path.join(src_data, latest_root)
    dst = os.path.join(dst_data, latest_root)
    if not os.path.exists(dst) or _file_differs(src, dst):
        shutil.copy2(src, dst)

    # Actualizar pointers al root
    pointer_txt = os.path.join(tenant_dir, "latest-json-filename.txt")
    with open(pointer_txt, "w", encoding="utf-8") as f:
        f.write("data/" + latest_root + "\n")
    pointer_json = os.path.join(tenant_dir, "latest-json-filename.json")
    with open(pointer_json, "w", encoding="utf-8") as f:
        json.dump({"filename": "data/" + latest_root}, f)

    # Borrar .gz viejos del tenant (mantener solo el ultimo del root).
    # OJO: si el tenant tenia uno mas reciente, ya retornamos arriba; aca solo
    # caemos cuando root > tenant, asi que el latest del tenant es seguro borrar.
    for f in os.listdir(dst_data):
        if f.endswith(".gz") and f != latest_root:
            try:
                os.remove(os.path.join(dst_data, f))
            except OSError:
                pass
    return True


def _walk_files(root):
    """Yield (rel_path con prefijo '/', abs_path) por cada archivo a deployar."""
    for d, _dirs, files in os.walk(root):
        for f in files:
            abs_path = os.path.join(d, f)
            rel = "/" + os.path.relpath(abs_path, root).replace(os.sep, "/")
            yield rel, abs_path


def _sha1_file(path):
    import hashlib
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def deploy_to_netlify(tenant_dir, site_id, token):
    """Sube el contenido de tenant_dir como un nuevo deploy a Netlify usando
    el protocolo 'digest' (POST con manifest + PUT por archivo nuevo).
    Reemplaza el viejo zip-upload que Netlify ya no permite con tokens nuevos.

    Retorna (ok, message). Silently skip si falta token o site_id.
    """
    if not token or not site_id:
        return (False, "sin token o site_id")
    try:
        import requests
    except ImportError:
        return (False, "requests no instalado")

    # 1. Calcular sha1 de cada archivo del tenant_dir.
    files_map = {}            # rel_path -> sha1
    paths_by_sha = {}         # sha1 -> abs_path (para subir despues)
    for rel, abs_p in _walk_files(tenant_dir):
        s = _sha1_file(abs_p)
        files_map[rel] = s
        paths_by_sha[s] = abs_p
    if not files_map:
        return (False, "tenant_dir vacio, nada que deployar")

    auth = {"Authorization": f"Bearer {token}"}

    # 2. POST manifest. Netlify responde con la lista 'required' de hashes
    # que falta subir (los demas los toma del deploy anterior).
    import time
    for attempt in range(4):
        try:
            res = requests.post(
                f"https://api.netlify.com/api/v1/sites/{site_id}/deploys",
                json={"files": files_map, "async": False},
                headers={**auth, "Content-Type": "application/json"},
                timeout=60,
            )
            break
        except requests.RequestException as e:
            if attempt == 3:
                return (False, f"manifest {type(e).__name__}: {e}")
            time.sleep(15)
    if not res.ok:
        return (False, f"manifest HTTP {res.status_code}: {res.text[:200]}")
    body = res.json()
    deploy_id = body.get("id")
    required = body.get("required") or []

    # 3. PUT cada archivo en required.
    for sha in required:
        path = paths_by_sha.get(sha)
        if not path:
            continue
        with open(path, "rb") as fh:
            for attempt in range(4):
                fh.seek(0)
                try:
                    r = requests.put(
                        f"https://api.netlify.com/api/v1/deploys/{deploy_id}/files{[k for k,v in files_map.items() if v==sha][0]}",
                        data=fh.read(),
                        headers={**auth, "Content-Type": "application/octet-stream"},
                        timeout=120,
                    )
                    break
                except requests.RequestException as e:
                    if attempt == 3:
                        return (False, f"upload {type(e).__name__}: {e}")
                    time.sleep(15)
        if not r.ok:
            return (False, f"upload HTTP {r.status_code} en {sha[:8]}: {r.text[:120]}")

    return (True, f"deploy_id={deploy_id} files={len(files_map)} uploaded={len(required)}")


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

        # Por ahora todos los tenants (testing y active) reciben mirror del data/
        # raiz porque update_products aun escribe ahi. Cuando refactoremos el
        # script para iterar tenants, los 'active' con supplier_account propio
        # dejaran de necesitar mirror.
        if state in ("testing", "active"):
            ok = mirror_data_to_tenant(slug, tenant_dir)
            print(f"[sync_tenants] {slug}: data mirror = {ok}")

        site_id = t.get("netlify_site_id")
        if site_id and netlify_token:
            ok, msg = deploy_to_netlify(tenant_dir, site_id, netlify_token)
            tag = "OK" if ok else "FAIL"
            print(f"[sync_tenants] {slug}: netlify deploy {tag} - {msg}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

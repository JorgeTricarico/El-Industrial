#!/usr/bin/env python3
"""Validacion post-deploy: corrobora que CADA sitio publico sirve precios
identicos a los que la Pi acaba de calcular esta noche.

Este script habria atrapado en menos de 1 hora el bug del 27/04-17/05 donde
el-industrial.netlify.app sirvio data congelada 19 dias.

Para cada tenant active/testing en _registry.yml:
  1. Lee tenants/<slug>/data/<ultimo>.gz local (lo que la Pi acaba de escribir).
  2. Fetcha el .gz que sirve la web publica.
  3. Si las fechas (filename) difieren -> ALERTA: deploy no llego.
  4. Si los bytes difieren -> ALERTA: web sirviendo otra version.
  5. Descomprime ambos, toma 5 productos por codigo y compara precio. Si
     algun precio difiere -> ALERTA: corrupcion / cache stale.
  6. Si toda la web cae o devuelve HTTP != 200 -> ALERTA.

Si cualquier check falla, manda Telegram a los admins (clients.yml role=admin)
y retorna exit code != 0 para que el cron lo marque como fallo en cron_log.
"""
import gzip
import io
import json
import os
import random
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
TENANTS_DIR = os.path.join(BASE_DIR, "tenants")
REGISTRY = os.path.join(TENANTS_DIR, "_registry.yml")
SAMPLE_SIZE = 5

sys.path.insert(0, SCRIPT_DIR)
import requests  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(BASE_DIR, ".env"))


def load_tenants():
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


def read_local_gz(tenant_dir):
    """Devuelve (filename, raw_bytes, parsed_json) del ultimo .gz local del tenant."""
    data_dir = os.path.join(tenant_dir, "data")
    if not os.path.isdir(data_dir):
        return (None, None, None)
    gz_files = sorted([f for f in os.listdir(data_dir) if f.endswith(".gz")], reverse=True)
    if not gz_files:
        return (None, None, None)
    fname = gz_files[0]
    path = os.path.join(data_dir, fname)
    with open(path, "rb") as f:
        raw = f.read()
    try:
        parsed = json.loads(gzip.decompress(raw).decode("utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return (fname, raw, {"_parse_error": str(e)})
    return (fname, raw, parsed)


def fetch_public(url, path, timeout=15):
    """GET url/path. Retorna (ok, status, content_bytes_or_text)."""
    try:
        res = requests.get(url.rstrip("/") + "/" + path.lstrip("/"), timeout=timeout)
    except Exception as e:
        return (False, 0, f"network_error: {type(e).__name__}: {e}")
    return (res.ok, res.status_code, res.content if res.ok else res.text[:200])


def normalize(items):
    """Devuelve dict {producto_key: precio_str}. La lista del JSON puede ser
    una lista de dicts; soportamos las dos formas que vimos en data/."""
    if isinstance(items, dict):
        # Algunos .gz son dict {codigo: {...}}
        return {k: str(v.get("precio") or v.get("precio_final") or v) for k, v in items.items()}
    out = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("producto") or item.get("code") or item.get("codigo") or item.get("Articulo_Corto")
        precio = item.get("precio") or item.get("precio_final") or item.get("Precio")
        if key is None or precio is None:
            continue
        out[str(key)] = str(precio)
    return out


def check_against_supplier(tenant, public_parsed):
    """Llama a la API del proveedor (Bertual u otra), aplica el config del tenant
    y compara un sample contra lo que la web publica sirve. Solo se ejecuta si
    estamos en un host con acceso al proveedor (la Pi, no GH Actions).

    Retorna lista de problemas (vacia si todo OK o si el proveedor no es accesible).
    """
    problems = []
    supplier = (tenant.get("supplier") or "").strip().lower()
    if supplier != "bertual":
        return []  # otros proveedores aun no implementados (Haedo, etc.)
    try:
        from bertual_api import BertualAPIClient
        import update_products as up
    except ImportError as e:
        return [f"{tenant.get('slug')}: no se pudo importar bertual_api: {e}"]
    try:
        client = BertualAPIClient()
        raw_products = client.fetch_products()
    except Exception as e:
        # API no accesible desde este host (timeout desde GH Actions es esperado).
        # Silencioso: no es un fallo del sistema, solo "no podemos verificar desde aca".
        print(f"[supplier] {tenant.get('slug')}: API del proveedor no accesible desde este host ({type(e).__name__}). Skip.")
        return []

    if not isinstance(raw_products, list) or not raw_products:
        return [f"{tenant.get('slug')}: API del proveedor devolvio respuesta inesperada o vacia."]

    # Aplicar transform_item del tenant. ATENCION: transform_item lee el config
    # GLOBAL (up.config), que apunta al config.json del raiz. Para multi-tenant
    # estricto deberiamos parsear el config del tenant. Por ahora, si el tenant
    # tiene un config propio, lo cargamos.
    tenant_dir = os.path.join(TENANTS_DIR, tenant.get("slug", ""))
    tenant_config_path = os.path.join(tenant_dir, "config", "config.json")
    if os.path.exists(tenant_config_path):
        try:
            with open(tenant_config_path, "r", encoding="utf-8") as f:
                up.config.update(json.load(f))
        except (OSError, json.JSONDecodeError):
            pass

    # Aplicar transform a TODOS los items del proveedor para tener el catalogo final
    supplier_normalized = {}
    for raw in raw_products:
        try:
            t = up.transform_item(raw)
            if t.get("producto"):
                supplier_normalized[str(t["producto"])] = str(t["precio"])
        except (TypeError, ValueError, KeyError):
            continue

    public_normalized = normalize(public_parsed)
    if not public_normalized:
        return [f"{tenant.get('slug')}: public_parsed vacio para comparar contra proveedor."]

    # Sample 10 productos del catalogo del proveedor y verifica que esten en la web
    # con el mismo precio. Si difieren mas de 1% (timing entre fetch y deploy), alerta.
    common_keys = list(set(supplier_normalized.keys()) & set(public_normalized.keys()))
    if not common_keys:
        return [f"{tenant.get('slug')}: el catalogo publico NO comparte productos con la API del proveedor. Posible corrupcion."]

    sample_size = min(10, len(common_keys))
    sample = random.sample(common_keys, sample_size)
    diffs = []
    for k in sample:
        try:
            p_sup = float(supplier_normalized[k])
            p_web = float(public_normalized[k])
        except ValueError:
            continue
        # Tolerancia: 1% de diferencia es OK (puede haber 1 peso por redondeo entre runs)
        if p_sup == 0:
            continue
        rel_diff = abs(p_sup - p_web) / p_sup
        if rel_diff > 0.01:
            diffs.append(f"{k}: proveedor={p_sup:.2f} web={p_web:.2f} ({rel_diff*100:.1f}% diff)")
    if diffs:
        problems.append(
            f"{tenant.get('slug')}: precios divergen del proveedor en {len(diffs)}/{sample_size} muestreos. "
            + " | ".join(diffs[:3])
        )
    else:
        print(f"[supplier] {tenant.get('slug')}: {sample_size} precios random matchean al proveedor (tolerancia 1%).")
    return problems


def check_tenant(tenant):
    """Retorna (slug, problems_list). Lista vacia si todo OK."""
    slug = tenant.get("slug")
    state = tenant.get("state")
    url = tenant.get("netlify_url", "")
    problems = []

    if state not in ("active", "testing"):
        return (slug, [])
    if not url.startswith("http"):
        problems.append(f"netlify_url ausente/invalido para {slug}.")
        return (slug, problems)

    tenant_dir = os.path.join(TENANTS_DIR, slug)
    local_fname, local_raw, local_data = read_local_gz(tenant_dir)
    if local_fname is None:
        problems.append(f"{slug}: no hay .gz local en {tenant_dir}/data/.")
        return (slug, problems)

    # 1) Pointer publico apunta al mismo archivo?
    ok, status, public_pointer = fetch_public(url, "latest-json-filename.txt")
    if not ok:
        problems.append(f"{slug}: sitio publico no responde (HTTP {status}: {public_pointer!r}).")
        return (slug, problems)
    public_pointer = public_pointer.decode("utf-8").strip() if isinstance(public_pointer, bytes) else public_pointer.strip()
    expected_pointer = "data/" + local_fname
    if public_pointer != expected_pointer:
        problems.append(
            f"{slug}: pointer publico apunta a {public_pointer!r} pero la Pi acaba de generar {expected_pointer!r}. "
            f"DEPLOY NO LLEGO o se desincronizo."
        )
        return (slug, problems)

    # 2) Fecha del filename: debe ser HOY o ayer (no mas de 26h)
    import re
    m = re.search(r"(\d{2}-\d{2}-\d{2})", local_fname)
    if m:
        yy, mm, dd = m.group(1).split("-")
        try:
            file_date = datetime.strptime(f"20{yy}-{mm}-{dd}", "%Y-%m-%d")
            age_h = (datetime.now() - file_date).total_seconds() / 3600
            if age_h > 26:
                problems.append(f"{slug}: data servida es del {file_date.date()} ({age_h:.0f}h atras). Update_products no esta corriendo o no escribe al tenant.")
        except ValueError:
            pass

    # 3) Bytes del .gz publico == bytes locales? Parseamos siempre para tener
    # public_parsed disponible para el check de proveedor abajo.
    ok, status, public_bytes = fetch_public(url, expected_pointer)
    if not ok:
        problems.append(f"{slug}: .gz publico no responde (HTTP {status}).")
        return (slug, problems)
    public_parsed = None
    try:
        public_parsed = json.loads(gzip.decompress(public_bytes).decode("utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    if public_parsed is None:
        try:
            public_parsed = json.loads(public_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            problems.append(f"{slug}: contenido publico no parsea (ni gzip ni json plano): {e}")
            return (slug, problems)

    local_norm = normalize(local_data) if isinstance(local_data, (list, dict)) else {}
    public_norm = normalize(public_parsed)
    if not local_norm:
        problems.append(f"{slug}: no se pudo normalizar la data local (formato inesperado).")
        return (slug, problems)
    if set(local_norm.keys()) != set(public_norm.keys()):
        missing = set(local_norm.keys()) - set(public_norm.keys())
        extra = set(public_norm.keys()) - set(local_norm.keys())
        problems.append(
            f"{slug}: catalogos no coinciden. Faltan en publico: {len(missing)} | Extra en publico: {len(extra)}. "
            f"Sample faltante: {list(missing)[:3]}"
        )
        return (slug, problems)
    # 4) Sample de precios entre local y publico
    keys = list(local_norm.keys())
    if keys:
        sample = random.sample(keys, min(SAMPLE_SIZE, len(keys)))
        diffs = []
        for k in sample:
            if local_norm[k] != public_norm.get(k):
                diffs.append(f"{k}: local={local_norm[k]} publico={public_norm.get(k)}")
        if diffs:
            problems.append(f"{slug}: precios difieren en sample. " + " | ".join(diffs))

    # 5) Verificacion contra el proveedor real (Bertual, Haedo, etc.)
    # Solo se ejecuta si el host tiene acceso al proveedor (la Pi). Silencioso si no.
    problems.extend(check_against_supplier(tenant, public_parsed))

    # 6) Smoke check del HTML publico
    problems.extend(check_html_smoke(tenant, url))

    return (slug, problems)


def check_html_smoke(tenant, url):
    """Fetch del index.html publico y verifica estructura minima.
    Atrapa: HTML corrupto, build incompleto, archivos JS faltantes.
    """
    problems = []
    slug = tenant.get("slug")
    try:
        res = requests.get(url, timeout=10)
    except Exception as e:
        return [f"{slug}: index.html no responde ({type(e).__name__})."]
    if not res.ok:
        return [f"{slug}: index.html HTTP {res.status_code}."]
    html = res.text
    required_markers = [
        ("<title>", "title tag"),
        ('id="brandName"', "brandName placeholder"),
        ('id="productTable"', "tabla de productos"),
        ('id="searchInput"', "search input"),
        ('src="js/main.js', "main.js linkeado"),
    ]
    for marker, desc in required_markers:
        if marker not in html:
            problems.append(f"{slug}: HTML publico le falta {desc} ({marker!r}). Build/deploy incompleto?")

    # Verifica que js/main.js carga sin 404
    try:
        res = requests.get(url.rstrip("/") + "/js/main.js", timeout=10)
        if not res.ok:
            problems.append(f"{slug}: js/main.js HTTP {res.status_code}. Front no funciona.")
    except Exception as e:
        problems.append(f"{slug}: js/main.js no fetcheable: {type(e).__name__}.")

    # Verifica branding.json sirve JSON valido
    try:
        res = requests.get(url.rstrip("/") + "/config/branding.json", timeout=10)
        if not res.ok:
            problems.append(f"{slug}: config/branding.json HTTP {res.status_code}.")
        else:
            j = res.json()
            if not j.get("siteName"):
                problems.append(f"{slug}: branding.json sin siteName. Web mostraria 'Cargando…'.")
    except Exception as e:
        problems.append(f"{slug}: branding.json no parsea como JSON: {type(e).__name__}.")
    return problems


def _log_alert(problems, recipients, sent_count, body):
    """Registra cada alerta enviada a status/alerts.jsonl para auditoria.

    Permite revisar despues 'que se le aviso al admin y cuando', sin depender
    de scrollear Telegram. El path apunta al status/ del repo raiz (esta es
    una accion administrativa, no por-tenant).
    """
    try:
        from timeutils import now_ar_iso
        ts = now_ar_iso()
    except ImportError:
        ts = datetime.now().isoformat()
    status_dir = os.path.join(BASE_DIR, "status")
    os.makedirs(status_dir, exist_ok=True)
    entry = {
        "ts": ts,
        "source": "post_deploy_check",
        "problems": problems,
        "recipients": [r[1] for r in recipients],
        "sent_count": sent_count,
        "body": body[:1000],
    }
    try:
        with open(os.path.join(status_dir, "alerts.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def send_alert(all_problems):
    """Manda alerta Telegram al admin via la cadena existente y registra a log."""
    if not all_problems:
        return False
    try:
        sys.path.insert(0, SCRIPT_DIR)
        import clients as _c
    except ImportError:
        return False
    token = os.getenv("TELEGRAM_TOKEN")
    legacy = os.getenv("TELEGRAM_CHAT_ID")
    if not token:
        return False
    recipients = _c.recipients_for("alert", legacy_chat_id=legacy)
    if not recipients:
        _log_alert(all_problems, [], 0, "(sin destinatarios configurados)")
        return False

    # Diagnostico AI con contexto del sistema. Solo va al admin/dev, NUNCA a clients.
    ai_text, ai_provider = "", "skip"
    try:
        import ai_diagnose
        ai_text, ai_provider = ai_diagnose.diagnose(all_problems)
    except Exception as e:
        ai_text = f"<i>(diagnostico AI no disponible: {type(e).__name__})</i>"
        ai_provider = "error"

    body = "🔴 <b>Post-deploy check FALLO</b> <i>(solo dev)</i>\n"
    body += f"Hora: {datetime.now().strftime('%d/%m %H:%M')} AR\n\n"
    body += "<b>Problemas detectados:</b>\n"
    body += "\n".join(f"• {p}" for p in all_problems)
    if ai_text:
        body += f"\n\n<b>Analisis AI ({ai_provider}):</b>\n{ai_text}"
    body += "\n\n<i>Los compradores NO reciben este mensaje. Solo admins.</i>"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    sent = 0
    for chat_id, _name in recipients:
        try:
            r = requests.post(url, data={"chat_id": chat_id, "text": body, "parse_mode": "HTML"}, timeout=15)
            if r.ok:
                sent += 1
        except Exception:
            pass
    _log_alert(all_problems, recipients, sent, body)
    return sent > 0


def main():
    tenants = load_tenants()
    if not tenants:
        print("[post_deploy] No hay tenants en _registry.yml")
        return 0
    all_problems = []
    for t in tenants:
        slug, problems = check_tenant(t)
        if problems:
            for p in problems:
                print(f"[FAIL] {p}")
            all_problems.extend(problems)
        else:
            if t.get("state") in ("active", "testing"):
                print(f"[OK] {slug}: web publica matchea data local")
    if all_problems:
        send_alert(all_problems)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

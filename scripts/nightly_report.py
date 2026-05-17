#!/usr/bin/env python3
"""Reporte ejecutivo nocturno con cadena de fallback de 3 LLMs + plantilla.

Cadena: Gemini 3.1 Flash-Lite -> Cerebras Qwen 2.5 72B -> Groq Llama 3.3 70B -> Plantilla.
La plantilla garantiza que SIEMPRE llega un mensaje a Telegram aunque caigan los 3 LLMs.
"""
import os, json, requests, time, socket
from datetime import datetime
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
STATUS_DIR = os.path.join(BASE_DIR, "status")
ENV_FILE = os.path.join(BASE_DIR, ".env")
TENANTS_DIR = os.path.join(BASE_DIR, "tenants")
REGISTRY = os.path.join(TENANTS_DIR, "_registry.yml")

load_dotenv(ENV_FILE)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")
SAMBANOVA_API_KEY = os.getenv("SAMBANOVA_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

HOST = socket.gethostname()


def log_metric(event, detail=""):
    """Append a structured event to status/metrics.jsonl."""
    os.makedirs(STATUS_DIR, exist_ok=True)
    entry = {"ts": datetime.now().isoformat(), "node": HOST, "event": event, "detail": str(detail)[:500]}
    try:
        with open(os.path.join(STATUS_DIR, "metrics.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[log_metric] no se pudo escribir metrics.jsonl: {e}")


def call_gemini(prompt):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY ausente")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite-preview:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    last_err = None
    for attempt in range(3):
        try:
            res = requests.post(url, json=payload, timeout=40)
            if res.status_code == 429:
                wait = (attempt + 1) * 10
                log_metric("llm_rate_limit", f"gemini attempt={attempt} wait={wait}s")
                time.sleep(wait)
                continue
            res.raise_for_status()
            text = res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            if text:
                return text
            last_err = "empty response"
        except (requests.RequestException, KeyError, ValueError) as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(2)
    raise RuntimeError(f"gemini agotado: {last_err}")


def call_cerebras(prompt):
    if not CEREBRAS_API_KEY:
        raise RuntimeError("CEREBRAS_API_KEY ausente")
    url = "https://api.cerebras.ai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {CEREBRAS_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "qwen-2.5-72b", "messages": [{"role": "user", "content": prompt}]}
    res = requests.post(url, json=payload, headers=headers, timeout=30)
    res.raise_for_status()
    text = res.json()["choices"][0]["message"]["content"].strip()
    if not text:
        raise RuntimeError("cerebras: respuesta vacia")
    return text


def call_sambanova(prompt):
    if not SAMBANOVA_API_KEY:
        raise RuntimeError("SAMBANOVA_API_KEY ausente")
    url = "https://api.sambanova.ai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {SAMBANOVA_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "Meta-Llama-3.3-70B-Instruct", "messages": [{"role": "user", "content": prompt}]}
    res = requests.post(url, json=payload, headers=headers, timeout=30)
    res.raise_for_status()
    text = res.json()["choices"][0]["message"]["content"].strip()
    if not text:
        raise RuntimeError("sambanova: respuesta vacia")
    return text


PROVIDERS = [
    ("gemini", call_gemini),
    ("cerebras", call_cerebras),
    ("sambanova", call_sambanova),
]


def render_template_fallback(updated_items, top_brands, top_hikes, fecha):
    """Mensaje plantilla cuando los 3 LLMs fallan. Garantiza entrega a Telegram."""
    lines = [f"<b>Resumen del dia — {fecha}</b>",
             f"{len(updated_items)} productos actualizados.", ""]
    if top_brands:
        lines.append("<b>Marcas con mas cambios:</b>")
        for brand, count in top_brands[:5]:
            lines.append(f"• {brand} ({count})")
        lines.append("")
    if top_hikes:
        lines.append("<b>Mayores subas:</b>")
        for h in top_hikes[:5]:
            nombre = (h.get("n") or "")[:60]
            pct = h.get("p", 0)
            lines.append(f"• {nombre}: {pct:+.1f}%")
        lines.append("")
    lines.append("<i>Reporte automatico (IA no disponible hoy)</i>")
    return "\n".join(lines)


def get_ai_analysis(prompt):
    """Cadena de fallback. Devuelve (texto, proveedor_usado). proveedor='template' si todos fallaron."""
    for name, fn in PROVIDERS:
        try:
            result = fn(prompt)
            log_metric("llm_used", name)
            return result, name
        except Exception as e:
            log_metric("llm_failed", f"{name}: {type(e).__name__}: {e}")
    return None, "template"


def sanitize_html(text):
    """Telegram HTML solo permite b, i, u, s, code, pre, a. Quita Markdown residual y emojis de alarma."""
    text = text.replace("*", "").replace("_", "")
    for forbidden in ("⚠️", "🚨", "🔥", "💥"):
        text = text.replace(forbidden, "")
    return text.strip()


def _send_to_chat(chat_id, message, name=""):
    """Envia un mensaje a un solo chat_id, con fallback HTML -> plano.
    Retorna True si Telegram acepto el mensaje en alguno de los dos formatos.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        res = requests.post(url, data=payload, timeout=20)
        if res.ok:
            log_metric("telegram_sent", f"html to={name}({chat_id})")
            return True
        log_metric("telegram_html_fail", f"{name}({chat_id}) {res.status_code}: {res.text[:200]}")
    except requests.RequestException as e:
        log_metric("telegram_html_fail", f"{name}({chat_id}) {type(e).__name__}: {e}")
    plain = message.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    try:
        res = requests.post(url, data={"chat_id": chat_id, "text": plain}, timeout=20)
        if res.ok:
            log_metric("telegram_sent", f"plain to={name}({chat_id})")
            return True
        log_metric("telegram_plain_fail", f"{name}({chat_id}) {res.status_code}: {res.text[:200]}")
    except requests.RequestException as e:
        log_metric("telegram_plain_fail", f"{name}({chat_id}) {type(e).__name__}: {e}")
    return False


def send_telegram(message, clients_path=None):
    """Broadcast a destinatarios habilitados como report (admin + client).

    clients_path: ruta al clients.yml del tenant. Si None, usa el legacy del
                  repo raiz (config/clients.yml). Pasar
                  tenants/<slug>/config/clients.yml para multi-tenant.
    Retorna True si llego al menos a un destinatario.
    """
    if not TELEGRAM_TOKEN:
        log_metric("telegram_skip", "TELEGRAM_TOKEN ausente")
        return False
    # Import perezoso para no crear dependencia circular con tests.
    sys_path_added = False
    try:
        import clients as _clients_mod  # noqa: F401
    except ImportError:
        import sys
        sys.path.insert(0, SCRIPT_DIR)
        sys_path_added = True
        import clients as _clients_mod
    finally:
        if sys_path_added:
            import sys
            if SCRIPT_DIR in sys.path:
                sys.path.remove(SCRIPT_DIR)

    recipients = _clients_mod.recipients_for(
        "report", legacy_chat_id=TELEGRAM_CHAT_ID, path=clients_path,
    )
    if not recipients:
        log_metric("telegram_skip", "sin destinatarios configurados")
        return False

    sent_count = 0
    for chat_id, name in recipients:
        if _send_to_chat(chat_id, message, name=name):
            sent_count += 1
    log_metric("telegram_broadcast", f"sent={sent_count}/{len(recipients)}")
    return sent_count > 0


def build_prompt(updated_items, top_brands, top_hikes):
    return f"""Sos el asistente de un vendedor de ferreteria industrial PYME en Argentina.
Tu mensaje va a Telegram y el vendedor puede reenviarlo a clientes.

Tono: coloquial argentino, directo, util. Como un colega que avisa algo concreto,
NO como un analista de mercado. NUNCA uses palabras: "critico", "alarmante",
"advertencia", "riesgo", "historico", "sin precedentes", "masivo". NUNCA pongas
emojis de alarma. Como mucho un 📌 al inicio.

Datos de hoy:
- Productos actualizados: {len(updated_items)}
- Marcas mas movidas: {top_brands}
- Mayores subas (%, producto, marca): {top_hikes}

Devolveme exactamente:
1. Una linea de resumen (que movio hoy y a que tipo de cliente conviene avisar).
2. 3 a 5 bullets con los cambios concretos mas relevantes (producto + % subio/bajo).
3. Si hay algo accionable obvio (re-cotizar, avisar a un rubro), una linea final.

Formato: HTML simple solo con <b>negrita</b> y bullets con "• ". Maximo 1200 caracteres.
Sin introducciones tipo "Hola" ni cierres tipo "Saludos"."""


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


def _compute_stats(updated_items):
    """Calcula top_brands + top_hikes a partir del set updated."""
    stats_marcas = {}
    aumentos = []
    for item in updated_items:
        brand = item.get("marca") or item.get("brand") or item.get("Familia") or "Otras"
        stats_marcas[brand] = stats_marcas.get(brand, 0) + 1
        try:
            old_p = float(item.get("old", 0))
            new_p = float(item.get("new", 0))
            if old_p > 0:
                diff = ((new_p - old_p) / old_p) * 100
                aumentos.append({"n": item.get("name", brand), "p": diff, "m": brand})
        except (TypeError, ValueError) as e:
            log_metric("price_parse_fail", f"{item.get('code', '?')}: {e}")
    top_brands = sorted(stats_marcas.items(), key=lambda x: x[1], reverse=True)[:5]
    top_hikes = sorted(aumentos, key=lambda x: x["p"], reverse=True)[:15]
    return top_brands, top_hikes


def _update_telegram_heartbeat(provider, status_dir):
    """Dead-man-switch: registra que el mensaje se envio."""
    try:
        hb_path = os.path.join(status_dir, "heartbeat.json")
        hb = {}
        if os.path.exists(hb_path):
            with open(hb_path, "r", encoding="utf-8") as f:
                hb = json.load(f)
        hb["last_telegram_iso"] = datetime.now().isoformat()
        hb["last_telegram_provider"] = provider
        os.makedirs(status_dir, exist_ok=True)
        with open(hb_path, "w", encoding="utf-8") as f:
            json.dump(hb, f, indent=2)
    except (OSError, json.JSONDecodeError) as e:
        log_metric("heartbeat_update_fail", f"{type(e).__name__}: {e}")


def _archive_accum(accum_path, status_dir):
    """Mueve accum a status_dir/archive/ con timestamp."""
    archive_dir = os.path.join(status_dir, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        os.rename(accum_path, os.path.join(archive_dir, f"accum_{ts}.json"))
    except OSError as e:
        log_metric("archive_fail", f"{type(e).__name__}: {e}")
    prune_old_archives(archive_dir, days=90)


def process_tenant_report(tenant):
    """Genera y envia el reporte nocturno de UN tenant.

    Lee tenants/<slug>/status/daily_accum.json. Manda a destinatarios
    role=admin+client del tenants/<slug>/config/clients.yml. Si el tenant
    no tiene accum, retorna early sin error (es valido: dia sin cambios).

    Retorna dict con info: {slug, status, items, provider, sent}.
    """
    slug = tenant.get("slug")
    result = {"slug": slug, "status": "skip", "items": 0, "provider": None, "sent": False}

    if tenant.get("state") not in ("active",):
        result["status"] = f"skip_state_{tenant.get('state')}"
        return result

    tenant_status_dir = os.path.join(TENANTS_DIR, slug, "status")
    accum_path = os.path.join(tenant_status_dir, "daily_accum.json")
    if not os.path.exists(accum_path):
        log_metric("nightly_skip", f"{slug}: sin daily_accum.json")
        result["status"] = "no_accum"
        return result

    with open(accum_path, "r", encoding="utf-8") as f:
        accum_data = json.load(f)
    updated_items = list(accum_data.get("updated", {}).values())

    top_brands, top_hikes = _compute_stats(updated_items)
    now = datetime.now()
    fecha = now.strftime("%d/%m/%Y")
    hora = now.strftime("%H:%M")

    if len(updated_items) == 0:
        body = "Sin novedades hoy. No se detectaron cambios de precios ni productos nuevos."
        provider = "none"
    else:
        prompt = build_prompt(updated_items, top_brands, top_hikes)
        body, provider = get_ai_analysis(prompt)
        if body is None:
            body = render_template_fallback(updated_items, top_brands, top_hikes, fecha)
        else:
            body = sanitize_html(body)

    # Branding del tenant (siteName) para personalizar el header
    site_name = ""
    try:
        bp = os.path.join(TENANTS_DIR, slug, "config", "branding.json")
        if os.path.exists(bp):
            with open(bp, "r", encoding="utf-8") as f:
                site_name = (json.load(f) or {}).get("siteName", "")
    except (OSError, json.JSONDecodeError):
        pass
    header = f"📌 <b>Lista del dia — {site_name}</b>" if site_name else "📌 <b>Lista del dia</b>"
    full_report = f"{header} — {fecha} {hora}\n\n{body}"
    if len(full_report) > 3900:
        full_report = full_report[:3870] + "\n\n<i>(mensaje recortado)</i>"

    clients_yml = os.path.join(TENANTS_DIR, slug, "config", "clients.yml")
    sent = send_telegram(full_report, clients_path=clients_yml)
    log_metric("nightly_done", f"{slug} provider={provider} sent={sent} items={len(updated_items)}")

    if sent:
        _update_telegram_heartbeat(provider, STATUS_DIR)  # heartbeat es global
    _archive_accum(accum_path, tenant_status_dir)

    result.update(status="ok", items=len(updated_items), provider=provider, sent=sent)
    return result


def main():
    tenants = load_registry()
    if not tenants:
        log_metric("nightly_skip", "sin tenants en _registry.yml")
        return

    any_processed = False
    for t in tenants:
        if t.get("state") != "active":
            continue
        res = process_tenant_report(t)
        log_metric("nightly_tenant_done",
                   f"{res['slug']} status={res['status']} items={res['items']} sent={res['sent']}")
        any_processed = True

    if not any_processed:
        log_metric("nightly_skip", "ningun tenant active en _registry.yml")

    # GC de logs append-only (1x/dia, despues de mandar los reportes).
    try:
        import log_rotation
        for res in log_rotation.rotate_all():
            if res["rotated"]:
                log_metric("log_rotated", f"{res['path']} -> {res['archive_path']}")
    except Exception as e:
        log_metric("log_rotation_fail", f"{type(e).__name__}: {e}")


def prune_old_archives(archive_dir, days=90):
    """Borra archivos de archive_dir con mtime > N dias. Retorna cantidad eliminada."""
    if not os.path.isdir(archive_dir):
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for name in os.listdir(archive_dir):
        path = os.path.join(archive_dir, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    if removed:
        log_metric("archive_prune", f"removed={removed} cutoff={days}d")
    return removed


if __name__ == "__main__":
    main()

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


def render_template_fallback(updated_items, top_brands, top_hikes, fecha, magnitude=None):
    """Mensaje plantilla cuando los 3 LLMs fallan. Garantiza entrega a Telegram.
    Respeta la misma logica de magnitud para no recomendar nada en dia tranquilo.
    """
    if magnitude is None:
        magnitude = classify_magnitude(top_hikes)
    cls = magnitude["class"]
    # Dia tranquilo: una sola linea, sin bullets.
    if cls == "negligible":
        return (
            f"<b>Lista del dia — {fecha}</b>\n"
            f"Dia tranquilo. {len(updated_items)} producto(s) con cambios infimos "
            f"(promedio {magnitude['avg_abs_pct']}%, probable redondeo). "
            f"Nada para avisar.\n"
            f"<i>(IA no disponible hoy)</i>"
        )
    # Resto: bullets en pesos, cantidad segun magnitud.
    n_bullets = {"minor": 2, "moderate": 4, "strong": 5}.get(cls, 3)
    top_changes = []
    for item in updated_items:
        try:
            old_p = float(item.get("old", 0))
            new_p = float(item.get("new", 0))
            if old_p > 0:
                top_changes.append({
                    "name": (item.get("name") or "")[:60],
                    "old": old_p, "new": new_p,
                    "diff": abs(new_p - old_p),
                })
        except (TypeError, ValueError):
            continue
    top_changes.sort(key=lambda x: x["diff"], reverse=True)
    intro = {
        "minor": "Movimiento chico hoy.",
        "moderate": "Cambios normales del dia.",
        "strong": "Hoy hubo cambios importantes.",
    }[cls]
    lines = [
        f"<b>Lista del dia — {fecha}</b>",
        f"{intro} {len(updated_items)} producto(s) actualizado(s).",
        "",
    ]
    for c in top_changes[:n_bullets]:
        lines.append(f"• {c['name']}: ${c['old']:.2f} → ${c['new']:.2f}")
    if cls == "strong":
        lines.append("")
        lines.append("<i>Conviene chequear precios pasados a clientes antes de facturar.</i>")
    lines.append("")
    lines.append("<i>(IA no disponible hoy)</i>")
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


# Umbral en % por debajo del cual un cambio individual se considera "ruido"
# (redondeo del proveedor, ajuste por centavos). No amerita accion comercial.
NOISE_PCT = 0.5
# Umbral del cambio MEDIO global por debajo del cual el dia se clasifica
# como "ruidoso" (no hay movimiento real, solo recalculos).
MAGNITUDE_NEGLIGIBLE = 1.0   # < 1% promedio = dia sin novedades
MAGNITUDE_MINOR = 3.0        # < 3% promedio = movimiento chico
MAGNITUDE_MODERATE = 8.0     # < 8% promedio = movimiento normal
# >= MAGNITUDE_MODERATE -> dia "fuerte"


def classify_magnitude(top_hikes):
    """Clasifica el dia en base a magnitud de cambios.

    Devuelve dict con:
      class: 'negligible' | 'minor' | 'moderate' | 'strong'
      avg_abs_pct: promedio absoluto de % de cambio (para contexto)
      max_pct: maximo % observado (signo preservado)
      meaningful_count: cuantos items pasan NOISE_PCT
      noise_count: cuantos items estan POR DEBAJO de NOISE_PCT (redondeo)
    """
    if not top_hikes:
        return {"class": "negligible", "avg_abs_pct": 0.0, "max_pct": 0.0,
                "meaningful_count": 0, "noise_count": 0}
    abs_pcts = [abs(h["p"]) for h in top_hikes]
    avg = sum(abs_pcts) / len(abs_pcts)
    max_p = max(top_hikes, key=lambda h: abs(h["p"]))["p"]
    meaningful = sum(1 for p in abs_pcts if p >= NOISE_PCT)
    noise = len(abs_pcts) - meaningful
    if avg < MAGNITUDE_NEGLIGIBLE and abs(max_p) < MAGNITUDE_MINOR:
        cls = "negligible"
    elif avg < MAGNITUDE_MINOR:
        cls = "minor"
    elif avg < MAGNITUDE_MODERATE:
        cls = "moderate"
    else:
        cls = "strong"
    return {"class": cls, "avg_abs_pct": round(avg, 2),
            "max_pct": round(max_p, 2),
            "meaningful_count": meaningful, "noise_count": noise}


_MAGNITUDE_INSTRUCTIONS = {
    "negligible": (
        "DIA TRANQUILO. Los cambios son infimos (probable redondeo del proveedor). "
        "HACELO ASI: una sola linea diciendo que el dia fue tranquilo, "
        "que los cambios son minimos. SIN bullets. NO recomendes ninguna accion "
        "(prohibido 're-cotizar', 'revisar', 'avisar a clientes'). "
        "Ej: 'Dia tranquilo. Un par de productos se movieron unos centavos, "
        "nada para avisar.' Listo."
    ),
    "minor": (
        "MOVIMIENTO CHICO. Subas/bajas de pocos pesos. "
        "HACELO ASI: 1 linea de resumen + 2-3 bullets con los cambios mas "
        "notorios (el que mas cambio en pesos, no en %). NO uses palabras "
        "como 're-cotizar' o 'revisar pedidos'. Si pones recomendacion, que "
        "sea solo si hay UN producto que cambio mucho ($ concretos)."
    ),
    "moderate": (
        "MOVIMIENTO NORMAL del dia. "
        "HACELO ASI: 1 linea de resumen + 3-5 bullets con los productos que mas "
        "cambiaron (priorizar los de mayor cambio en pesos). Si hay alguno que "
        "vale la pena revisar antes de cobrarlo, decilo en 1 linea final, sin jerga."
    ),
    "strong": (
        "DIA CON MOVIMIENTO FUERTE. "
        "HACELO ASI: 1 linea diciendo que hoy hubo cambios importantes + "
        "top 5 bullets con los productos mas movidos (pesos viejos -> nuevos). "
        "1 linea final concreta: que conviene chequear precios viejos pasados a "
        "clientes antes de facturar."
    ),
}


def build_prompt(updated_items, top_brands, top_hikes, magnitude=None):
    if magnitude is None:
        magnitude = classify_magnitude(top_hikes)
    magnitude_block = _MAGNITUDE_INSTRUCTIONS[magnitude["class"]]
    # Top 5 con pesos viejo/nuevo para que el LLM pueda mostrar montos reales.
    top_changes = []
    for item in updated_items[:30]:
        try:
            old_p = float(item.get("old", 0))
            new_p = float(item.get("new", 0))
            if old_p > 0:
                top_changes.append({
                    "name": (item.get("name") or "")[:60],
                    "old": round(old_p, 2),
                    "new": round(new_p, 2),
                    "pct": round((new_p - old_p) / old_p * 100, 2),
                })
        except (TypeError, ValueError):
            continue
    top_changes.sort(key=lambda x: abs(x["new"] - x["old"]), reverse=True)
    top_changes = top_changes[:5]
    return f"""Sos el ayudante de un comerciante chico de ferreteria en Argentina.
Le mandas un Telegram corto contandole que se movio hoy en la lista del mayorista.

Quien lo lee NO es analista. Es un comerciante que tiene 30 segundos para verlo
entre cliente y cliente. Hablale como un amigo del local, no como consultor.

Reglas duras (no negociables):
- TONO: coloquial argentino, directo, en pesos. Sin jerga ("rubro", "cotizar",
  "ajuste", "recalibrar" -> fuera).
- PROHIBIDO: "critico", "alarmante", "riesgo", "historico", "masivo", emojis
  de alarma. Maximo 📌 al inicio.
- MAXIMO 600 caracteres total (mensaje corto, no informe largo).
- Sin "Hola" ni "Saludos". Va directo al grano.
- Cuando muestres cambios, en PESOS: "$1.075 -> $1.082". Si pones %, secundario.

Datos del dia:
- Productos actualizados: {len(updated_items)}
- Cambio promedio absoluto: {magnitude['avg_abs_pct']}%
- Cambio mas grande: {magnitude['max_pct']:+.2f}%
- Items significativos (>= {NOISE_PCT}%): {magnitude['meaningful_count']}
- Items de ruido (< {NOISE_PCT}%, redondeo): {magnitude['noise_count']}
- Top cambios en pesos: {top_changes}

{magnitude_block}

Formato: HTML simple solo con <b>negrita</b> y bullets con "• "."""


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
        import sys as _sys
        _sys.path.insert(0, SCRIPT_DIR)
        import heartbeat_io
        heartbeat_io.update_telegram(status_dir, provider, datetime.now().isoformat())
    except (OSError, ImportError) as e:
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
        magnitude = classify_magnitude(top_hikes)
        log_metric("magnitude", f"{slug} class={magnitude['class']} avg={magnitude['avg_abs_pct']}%")
        prompt = build_prompt(updated_items, top_brands, top_hikes, magnitude=magnitude)
        body, provider = get_ai_analysis(prompt)
        if body is None:
            body = render_template_fallback(updated_items, top_brands, top_hikes, fecha, magnitude=magnitude)
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

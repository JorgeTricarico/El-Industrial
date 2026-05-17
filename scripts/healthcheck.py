#!/usr/bin/env python3
"""Healthcheck: alerta directa por Telegram cuando el sistema esta en problemas.

Dispara alerta si:
- heartbeat.json no existe o tiene > 26 horas.
- ultimas 3 corridas en metrics.jsonl tuvieron api == "api_fail".
- heartbeat.status != "ok" en la ultima corrida.

Diseñado para correr:
- En la Pi via cron matinal: `0 8 * * *`
- En GitHub Actions como step de failover.yml
"""
import os, json, sys, socket, subprocess
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
STATUS_DIR = os.path.join(BASE_DIR, "status")
ENV_FILE = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_FILE)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
THRESHOLD_HOURS = 26  # tolera un dia + 2h de margen

HOST = socket.gethostname()


def read_heartbeat():
    """Lee heartbeat normalizado al schema multi-nodo. None si no existe."""
    sys.path.insert(0, SCRIPT_DIR)
    try:
        import heartbeat_io
    finally:
        if SCRIPT_DIR in sys.path:
            sys.path.remove(SCRIPT_DIR)
    hb = heartbeat_io.read(STATUS_DIR)
    if not hb.get("nodes") and "last_telegram_iso" not in hb:
        return None
    return hb


def hours_since(iso_ts):
    try:
        ts = datetime.fromisoformat(iso_ts)
        return (datetime.now() - ts).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def last_n_runs(n=3):
    """Lee las ultimas N entradas con event=='log_metrics' o api status en metrics.jsonl."""
    path = os.path.join(STATUS_DIR, "metrics.jsonl")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    parsed = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
            # Solo nos interesan las corridas de update_products (que tienen campo "api")
            if "api" in entry:
                parsed.append(entry)
                if len(parsed) >= n:
                    break
        except json.JSONDecodeError:
            continue
    return parsed


def detect_version_drift(heartbeat):
    """Compara la version de CADA nodo en el heartbeat con origin/main.

    Hace git fetch una vez y compara. Devuelve lista de mensajes (uno por nodo
    con drift). Si falla la red o git, retorna [] silenciosamente.
    """
    if not heartbeat:
        return []
    nodes = heartbeat.get("nodes", {})
    if not nodes:
        return []
    try:
        subprocess.check_call(
            ["git", "-C", BASE_DIR, "fetch", "origin", "--quiet"],
            timeout=15, stderr=subprocess.DEVNULL,
        )
        remote_ver = subprocess.check_output(
            ["git", "-C", BASE_DIR, "rev-parse", "--short", "origin/main"],
            timeout=5, stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if not remote_ver:
        return []
    drifts = []
    for name, entry in nodes.items():
        node_ver = entry.get("version", "")
        if not node_ver or node_ver == "unknown":
            continue
        if not (node_ver.startswith(remote_ver) or remote_ver.startswith(node_ver)):
            drifts.append(
                f"Drift de version: el nodo '{name}' corrio HEAD={node_ver} "
                f"pero origin/main esta en {remote_ver}. El nodo no esta pulleando."
            )
    return drifts


def diagnose():
    """Devuelve (status, mensaje). status='ok' o 'alert'."""
    problems = []

    hb = read_heartbeat()
    if hb is None or not hb.get("nodes"):
        problems.append("Sin heartbeat. Ningun nodo reporto aun.")
    else:
        # Multi-nodo: si TODOS los nodos tienen last_run > umbral, alertamos.
        # Si al menos uno corrio reciente, OK (el sistema esta vivo aunque
        # alguno del clúster este caido — eso lo cubre system_audit).
        ages = []
        for name, entry in hb["nodes"].items():
            age = hours_since(entry.get("last_run", ""))
            if age is not None:
                ages.append((name, age, entry.get("status", "ok")))
        if not ages:
            problems.append("heartbeat: ningun nodo con last_run parseable.")
        else:
            min_age_node, min_age, _ = min(ages, key=lambda x: x[1])
            if min_age > THRESHOLD_HOURS:
                detail = ", ".join(f"{n}:{a:.1f}h" for n, a, _ in ages)
                problems.append(
                    f"Heartbeat viejo: nodo mas reciente ({min_age_node}) hace {min_age:.1f}h "
                    f"(umbral {THRESHOLD_HOURS}h). Detalle: {detail}."
                )
            # Status no-ok en el ultimo run de cada nodo
            for name, _age, status in ages:
                if status != "ok":
                    problems.append(f"Ultima corrida con status='{status}' (nodo {name}).")

        # Dead-man-switch: telegram global (cualquier nodo lo manda)
        tg_iso = hb.get("last_telegram_iso")
        if tg_iso:
            tg_age = hours_since(tg_iso)
            if tg_age is not None and tg_age > THRESHOLD_HOURS:
                problems.append(
                    f"Telegram no se envio hace {tg_age:.1f}h "
                    f"(ultimo proveedor: {hb.get('last_telegram_provider', '?')})."
                )

    last_runs = last_n_runs(3)
    if last_runs and all(r.get("api") == "api_fail" for r in last_runs):
        problems.append(f"Las ultimas {len(last_runs)} corridas fallaron contra la API Bertual.")

    problems.extend(detect_version_drift(hb))

    stale = detect_public_site_stale()
    problems.extend(stale)

    if problems:
        return "alert", problems
    return "ok", []


def detect_public_site_stale():
    """Para cada tenant active/testing en _registry.yml, fetch su pointer publico
    (https://<url>/latest-json-filename.txt) y compara con el pointer local del
    repo. Si el publico apunta a un archivo mas viejo que THRESHOLD_HOURS, alerta.

    Esto cubre el bug del 27/04-17/05 donde el sitio sirvio data congelada
    porque los deploys de Netlify fallaban silenciosamente.
    """
    problems = []
    registry = os.path.join(BASE_DIR, "tenants", "_registry.yml")
    if not os.path.exists(registry):
        return problems
    try:
        import yaml
    except ImportError:
        return problems
    try:
        with open(registry, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        return problems
    for t in data.get("tenants", []):
        if t.get("state") not in ("active", "testing"):
            continue
        url = t.get("netlify_url", "")
        if not url.startswith("http"):
            continue
        try:
            res = requests.get(url.rstrip("/") + "/latest-json-filename.txt", timeout=10)
        except Exception as e:
            problems.append(f"No se pudo consultar sitio de {t.get('slug')}: {type(e).__name__}.")
            continue
        if not res.ok:
            problems.append(f"Sitio publico de {t.get('slug')} no responde (HTTP {res.status_code}).")
            continue
        public_filename = res.text.strip()
        # Extraer la fecha del nombre del archivo: lista_precio_YY-MM-DD_...
        import re
        m = re.search(r"(\d{2}-\d{2}-\d{2})", public_filename)
        if not m:
            continue
        yy, mm, dd = m.group(1).split("-")
        try:
            file_date = datetime.strptime(f"20{yy}-{mm}-{dd}", "%Y-%m-%d")
        except ValueError:
            continue
        age_h = (datetime.now() - file_date).total_seconds() / 3600
        if age_h > THRESHOLD_HOURS:
            problems.append(
                f"Sitio publico {t.get('slug')} sirve data del {file_date.date()} "
                f"({age_h:.0f}h atras). El deploy a Netlify NO esta llegando."
            )
    return problems


def send_alert(problems):
    """Envia alerta SOLO a los destinatarios con role=admin habilitados.
    Las alertas tecnicas no van a los clientes pagos.
    """
    if not TELEGRAM_TOKEN:
        print("[telegram] TELEGRAM_TOKEN ausente. No se puede alertar.", file=sys.stderr)
        return False
    sys.path.insert(0, SCRIPT_DIR)
    try:
        import clients as _clients_mod
    finally:
        if SCRIPT_DIR in sys.path:
            sys.path.remove(SCRIPT_DIR)
    recipients = _clients_mod.recipients_for("alert", legacy_chat_id=TELEGRAM_CHAT_ID)
    if not recipients:
        print("[telegram] sin destinatarios admin configurados.", file=sys.stderr)
        return False

    # Rate-limit: si el mismo set de problemas se mando hace < N min, no spamear.
    try:
        sys.path.insert(0, SCRIPT_DIR)
        import alert_throttle
        send_ok, reason = alert_throttle.should_send(problems)
        if not send_ok:
            print(f"[telegram] throttled: {reason}", file=sys.stderr)
            return False
    except ImportError:
        pass  # sin throttle disponible, mandar igual

    # Diagnostico AI con contexto. Solo admin/dev, nunca cliente.
    ai_text, ai_provider = "", "skip"
    try:
        sys.path.insert(0, SCRIPT_DIR)
        import ai_diagnose
        ai_text, ai_provider = ai_diagnose.diagnose(problems)
    except Exception as e:
        ai_text = f"<i>(diagnostico AI no disponible: {type(e).__name__})</i>"

    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    body = f"🔧 <b>Healthcheck — {now} AR</b> <i>(solo dev)</i>\nNodo: {HOST}\n\n"
    body += "<b>Problemas:</b>\n" + "\n".join(f"• {p}" for p in problems)
    if ai_text:
        body += f"\n\n<b>Analisis AI ({ai_provider}):</b>\n{ai_text}"
    body += "\n\nLogs: <code>reports/cron_log.txt</code> y <code>status/metrics.jsonl</code>."
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    sent_count = 0
    for chat_id, _name in recipients:
        try:
            res = requests.post(url, data={"chat_id": chat_id, "text": body, "parse_mode": "HTML"}, timeout=15)
            if res.ok:
                sent_count += 1
        except requests.RequestException as e:
            print(f"[telegram] fallo a {chat_id}: {e}", file=sys.stderr)
    return sent_count > 0


def main():
    status, problems = diagnose()
    if status == "ok":
        print("OK: sistema saludable.")
        return 0
    print("ALERTA: problemas detectados:")
    for p in problems:
        print(f"  - {p}")
    if send_alert(problems):
        print("Alerta Telegram enviada.")
        return 1  # exit code != 0 para que CI lo marque como fallo
    print("ERROR: no se pudo enviar alerta Telegram.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

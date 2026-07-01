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
load_dotenv(ENV_FILE, override=True)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
THRESHOLD_HOURS = 26  # tolera un dia + 2h de margen
# Fallo sostenido del proveedor: cuantas corridas seguidas sin precios hacen
# falta para escalar. Un supplier_down aislado es esperado (lo cubre el filler
# Lun-Sab); recien alertamos cuando el proveedor esta caido de forma sostenida.
SUSTAINED_FAIL_RUNS = 3
SUSTAINED_FAIL_STATES = ("api_fail", "supplier_down")

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

    NO alerta si el nodo simplemente todavia no corrio cron desde el ultimo
    commit (caso normal: commit a las 15:00, cron del nodo a las 20:00; en
    el medio el healthcheck no debe alarmar). Solo alerta si el nodo
    `last_pulled_iso` es POSTERIOR al commit pero la version siguio vieja
    — eso significa git pull esta roto.

    Si falla la red o git, retorna [] silenciosamente.
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
        # Timestamp del ultimo commit en origin/main (ISO).
        remote_commit_iso = subprocess.check_output(
            ["git", "-C", BASE_DIR, "log", "-1", "--format=%cI", "origin/main"],
            timeout=5, stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []
    if not remote_ver:
        return []
    try:
        remote_dt = datetime.fromisoformat(remote_commit_iso)
        # Convertir a naive local time para comparar con los ISO del heartbeat
        # que estan en TZ local sin offset.
        if remote_dt.tzinfo is not None:
            remote_dt = remote_dt.astimezone().replace(tzinfo=None)
    except (ValueError, TypeError):
        remote_dt = None

    drifts = []
    for name, entry in nodes.items():
        node_ver = entry.get("version", "")
        if not node_ver or node_ver == "unknown":
            continue
        if node_ver.startswith(remote_ver) or remote_ver.startswith(node_ver):
            continue  # version OK
        # Hay diferencia de version. ¿El nodo pulleo DESPUES del commit?
        last_pulled = entry.get("last_pulled_iso") or entry.get("last_run", "")
        node_pulled_after_commit = False
        if last_pulled and remote_dt:
            try:
                node_dt = datetime.fromisoformat(last_pulled.replace("Z", ""))
                if node_dt.tzinfo is not None:
                    node_dt = node_dt.astimezone().replace(tzinfo=None)
                # 1 min de tolerancia por skew de clocks
                node_pulled_after_commit = node_dt > remote_dt + timedelta(minutes=1)
            except (ValueError, TypeError):
                pass
        if node_pulled_after_commit:
            drifts.append(
                f"Drift de version: el nodo '{name}' corrio HEAD={node_ver} "
                f"pero origin/main esta en {remote_ver}. El nodo no esta pulleando."
            )
        # Si no pulleo despues del commit, NO alertamos: es normal que el nodo
        # tenga version vieja mientras espera su proximo cron.
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

    # Fallo SOSTENIDO del proveedor: N corridas seguidas sin obtener precios.
    # Cubre supplier_down (timeout/500) Y api_fail (error de la API). Antes solo
    # miraba api_fail, dejando pasar un outage sostenido de supplier_down hasta
    # que detect_public_site_stale lo agarraba recien a las 26h+ (gap P13,
    # 2026-07-01). Un supplier_down aislado NO alerta (lo cubre el filler
    # Lun-Sab); recien escalamos con >= SUSTAINED_FAIL_RUNS corridas seguidas.
    last_runs = last_n_runs(SUSTAINED_FAIL_RUNS)
    if (len(last_runs) >= SUSTAINED_FAIL_RUNS
            and all(r.get("api") in SUSTAINED_FAIL_STATES for r in last_runs)):
        estados = ", ".join(r.get("api", "?") for r in last_runs)
        problems.append(
            f"Las ultimas {len(last_runs)} corridas fallaron contra la API Bertual "
            f"(estados: {estados}). Proveedor caido sostenido — el filler cubre al "
            f"cliente pero la data NO se esta actualizando."
        )

    problems.extend(detect_version_drift(hb))

    stale = detect_public_site_stale()
    problems.extend(stale)

    if problems:
        return "alert", problems
    return "ok", []


def _expected_stale_hours(now=None):
    """Cuanto puede tener la data publica sin que sea sospechoso, segun el
    momento de la semana. El cron es Lun-Sab → Sabado a la noche es la
    ultima corrida hasta Lunes 20:00 AR. Mientras tanto la data esta
    'fresca por diseño' aunque tenga 40+ horas.

    Default: THRESHOLD_HOURS (26h). Domingo/Lunes-temprano: hasta 50h.
    """
    now = now or datetime.now()
    wd = now.weekday()  # 0=Lun..6=Dom
    # Domingo todo el dia: ultimo cron fue Sab ~22:00, tolerancia amplia.
    if wd == 6:
        return 50
    # Lunes antes del cron de las 20:00 AR: data sigue siendo del Sabado.
    if wd == 0 and now.hour < 20:
        return 50
    return THRESHOLD_HOURS


def detect_public_site_stale():
    """Para cada tenant active en _registry.yml, fetch su pointer publico
    (https://<url>/latest-json-filename.txt) y verifica que no sea obsoleto.

    Umbral default 26h, pero Domingo y Lunes-antes-de-las-20 se relaja a 50h
    porque el cron es Lun-Sab (no actualiza Domingo).

    La edad se mide desde file_date + 20h en vez de medianoche: el Pi puede
    correr hasta las 22:00 AR, asi que el archivo mas nuevo de un dia tiene
    como minimo 20h de gracia. Sin esto, cualquier archivo con fecha de ayer
    parece 26h+ stale al amanecer aunque el deploy fue reciente.

    Tenants state=testing se saltean: su data es estatica por diseno y no
    representan un fallo de deploy (cubre el falso positivo de demo-electricidad).

    Esto cubre el bug del 27/04-17/05 donde el sitio sirvio data congelada
    porque los deploys de Netlify fallaban silenciosamente.
    """
    from datetime import timedelta
    threshold_h = _expected_stale_hours()
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
        if t.get("state") != "active":
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
        # +20h: el Pi corre hasta las 22:00 AR; medir desde medianoche
        # genera falsos positivos cuando GH Actions arranca tarde al dia siguiente.
        effective_deploy = file_date + timedelta(hours=20)
        age_h = max(0, (datetime.now() - effective_deploy).total_seconds() / 3600)
        if age_h > threshold_h:
            problems.append(
                f"Sitio publico {t.get('slug')} sirve data del {file_date.date()} "
                f"({age_h:.0f}h desde ultimo deploy posible, umbral hoy {threshold_h:.0f}h). "
                "El deploy a Netlify NO esta llegando."
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

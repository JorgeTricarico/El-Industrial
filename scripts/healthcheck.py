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
import os, json, sys, socket
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
    path = os.path.join(STATUS_DIR, "heartbeat.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[heartbeat] no se pudo leer: {e}", file=sys.stderr)
        return None


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


def diagnose():
    """Devuelve (status, mensaje). status='ok' o 'alert'."""
    problems = []

    hb = read_heartbeat()
    if hb is None:
        problems.append("Sin heartbeat.json. El sistema nunca corrio o el archivo se perdio.")
    else:
        age = hours_since(hb.get("last_run", ""))
        if age is None:
            problems.append(f"heartbeat.last_run invalido: {hb.get('last_run')!r}")
        elif age > THRESHOLD_HOURS:
            problems.append(f"Heartbeat viejo: {age:.1f}h (umbral {THRESHOLD_HOURS}h). Ultimo nodo: {hb.get('node', '?')}.")

        status = hb.get("status", "ok")
        if status != "ok":
            problems.append(f"Ultima corrida con status='{status}' (nodo {hb.get('node', '?')}).")

        # Dead-man-switch: verificar que el Telegram efectivamente se envio.
        # Es posible que update_products corra ok pero el envio a Telegram falle.
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

    if problems:
        return "alert", problems
    return "ok", []


def send_alert(problems):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("[telegram] credenciales ausentes. No se puede alertar.", file=sys.stderr)
        return False
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    body = f"🔧 <b>El Industrial — chequeo {now}</b>\nNodo: {HOST}\n\n"
    body += "\n".join(f"• {p}" for p in problems)
    body += "\n\nRevisar logs en <code>reports/cron_log.txt</code> y <code>status/metrics.jsonl</code>."
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        res = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": body, "parse_mode": "HTML"}, timeout=15)
        return res.ok
    except requests.RequestException as e:
        print(f"[telegram] fallo: {e}", file=sys.stderr)
        return False


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

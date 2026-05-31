#!/usr/bin/env python3
"""Self-Healing Watchdog — El Industrial.

Detecta que los precios no se actualizaron en las últimas 26h y toma acción
autonoma para corregirlo sin intervención humana:

1. Diagnostica la causa (git bloqueado, supplier down, dep error, etc.)
2. Intenta remediar (cleanup git, reinstalar deps, reintentar update_products)
3. Reporta TODAS las acciones tomadas a Telegram (con resultado)
4. Escribe eventos estructurados en status/metrics.jsonl

Invocado desde run_frequent.sh si la data está stale, o manualmente.
También puede invocarse como cron separado cada 6h como segunda red de seguridad.

Filosofía: preferimos que el agente actúe y se equivoque a que los precios
queden desactualizados. Toda acción queda loggeada.
"""

import os
import sys
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPT_DIR.parent
STATUS_DIR = BASE_DIR / "status"
LOG_FILE = BASE_DIR / "reports" / "cron_log.txt"

# Cargar .env manualmente para no depender del venv al arrancar
def _load_env():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k, v)

_load_env()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
HOSTNAME = os.uname().nodename


def log(msg: str):
    """Log al cron_log.txt y stdout."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{HOSTNAME}] [WATCHDOG] {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def metric(event: str, detail: str = ""):
    """Escribe evento estructurado en metrics.jsonl."""
    STATUS_DIR.mkdir(exist_ok=True)
    entry = {
        "ts": datetime.now().isoformat(),
        "node": HOSTNAME,
        "event": event,
        "detail": detail[:600],
    }
    try:
        with open(STATUS_DIR / "metrics.jsonl", "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def send_telegram(text: str):
    """Envía mensaje a Telegram. Nunca falla silenciosamente."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("WARNING: Telegram no configurado, no se puede notificar.")
        return False
    import urllib.request
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    try:
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        log(f"ERROR: Telegram falló: {e}")
        return False


def run_cmd(cmd: list, cwd=None, timeout=120) -> tuple[int, str, str]:
    """Ejecuta comando, devuelve (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd, cwd=cwd or BASE_DIR, capture_output=True,
            text=True, timeout=timeout
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except Exception as e:
        return -1, "", str(e)


def get_data_age_hours() -> float | None:
    """Devuelve antigüedad en horas del último .gz en el tenant el-industrial."""
    tenant_data = BASE_DIR / "tenants" / "el-industrial" / "data"
    if not tenant_data.exists():
        return None
    gz_files = sorted(tenant_data.glob("*.gz"))
    if not gz_files:
        return None
    latest = gz_files[-1]
    mtime = datetime.fromtimestamp(latest.stat().st_mtime)
    age = (datetime.now() - mtime).total_seconds() / 3600
    return round(age, 1)


def check_git_untracked_blocking() -> list[str]:
    """Detecta .gz untrackeados que bloquearían un pull."""
    rc, out, _ = run_cmd(["git", "status", "--porcelain"])
    blocking = []
    for line in out.splitlines():
        if line.startswith("??") and line.endswith(".gz"):
            path = line[3:].strip()
            blocking.append(path)
    return blocking


def cleanup_untracked_gz() -> list[str]:
    """Elimina .gz untrackeados que existen en origin/main. Seguro: ya están en el repo."""
    blocking = check_git_untracked_blocking()
    cleaned = []
    for rel_path in blocking:
        full_path = BASE_DIR / rel_path
        # Verificar que existe en origin antes de eliminar
        rc, _, _ = run_cmd(["git", "show", f"origin/main:{rel_path}"])
        if rc == 0 and full_path.exists():
            full_path.unlink()
            log(f"SELFHEAL: eliminado untracked {rel_path} (ya existe en origin/main)")
            metric("selfheal_git_cleanup", f"removed untracked: {rel_path}")
            cleaned.append(rel_path)
    return cleaned


def attempt_git_pull() -> tuple[bool, str]:
    """Intenta git pull --rebase --autostash. Devuelve (success, mensaje)."""
    rc, out, err = run_cmd(
        ["git", "pull", "--rebase", "--autostash", "origin", "main"],
        timeout=60
    )
    if rc == 0:
        return True, out or "OK"
    return False, err or out


def attempt_update_products() -> tuple[bool, str]:
    """Ejecuta update_products.py. Devuelve (success, salida combinada)."""
    venv_py = BASE_DIR / "venv" / "bin" / "python3"
    py_bin = str(venv_py) if venv_py.exists() else "python3"
    rc, out, err = run_cmd(
        [py_bin, str(SCRIPT_DIR / "update_products.py")],
        timeout=200
    )
    combined = (out + "\n" + err).strip()[:800]
    return rc == 0, combined


def diagnose() -> dict:
    """Diagnóstico rápido del estado del sistema."""
    diag = {}

    # 1. Antigüedad de datos
    age = get_data_age_hours()
    diag["data_age_hours"] = age
    diag["data_stale"] = age is None or age > 26

    # 2. Git state
    blocking = check_git_untracked_blocking()
    diag["git_untracked_blocking"] = blocking

    rc, out, err = run_cmd(["git", "status", "--short"])
    diag["git_status"] = out[:200]

    # 3. Última entrada en metrics
    metrics_path = STATUS_DIR / "metrics.jsonl"
    if metrics_path.exists():
        with open(metrics_path) as f:
            lines = f.readlines()
        last_events = [json.loads(l) for l in lines[-5:] if l.strip()]
        diag["last_events"] = last_events
    else:
        diag["last_events"] = []

    # 4. Último outcome en heartbeat
    hb_path = STATUS_DIR / "heartbeat.json"
    if hb_path.exists():
        with open(hb_path) as f:
            hb = json.load(f)
        node_info = hb.get("nodes", {}).get(HOSTNAME, {})
        diag["heartbeat_last_outcome"] = node_info.get("last_outcome", "unknown")
        diag["heartbeat_last_run"] = node_info.get("last_run", "unknown")
    else:
        diag["heartbeat_last_outcome"] = "no_heartbeat"
        diag["heartbeat_last_run"] = "unknown"

    return diag


def build_telegram_report(actions: list[dict], diag: dict) -> str:
    """Construye mensaje HTML para Telegram con las acciones tomadas."""
    lines = ["🤖 <b>Self-Healing Watchdog</b>", f"<i>Nodo: {HOSTNAME}</i>", ""]

    age = diag.get("data_age_hours")
    age_str = f"{age}h" if age else "desconocida"
    lines.append(f"⚠️ Data stale detectada (antigüedad: {age_str})")
    lines.append("")
    lines.append("<b>Acciones tomadas:</b>")

    for action in actions:
        icon = "✅" if action.get("success") else "❌"
        lines.append(f"{icon} {action['name']}: {action['result']}")

    any_success = any(a.get("success") for a in actions)
    lines.append("")
    if any_success:
        lines.append("✅ <b>Al menos una acción fue exitosa. Monitorear próximo cron.</b>")
    else:
        lines.append("🔴 <b>Todas las acciones fallaron. Requiere revisión manual.</b>")

    lines.append("")
    lines.append(f"<i>último outcome: {diag.get('heartbeat_last_outcome', '?')}</i>")
    return "\n".join(lines)


def main():
    log("Iniciando self-healing watchdog...")
    metric("watchdog_start", f"checking data freshness")

    diag = diagnose()
    age = diag.get("data_age_hours")

    if not diag.get("data_stale"):
        log(f"Data fresca ({age}h). No se requiere acción.")
        metric("watchdog_skip", f"data age={age}h OK")
        return 0

    log(f"Data STALE ({age}h). Iniciando secuencia de auto-remediación...")
    metric("watchdog_stale_detected", f"age={age}h outcome={diag.get('heartbeat_last_outcome')}")

    actions = []

    # --- Paso 1: Limpiar git si hay untracked bloqueantes ---
    if diag.get("git_untracked_blocking"):
        log(f"Paso 1: limpiando {len(diag['git_untracked_blocking'])} archivo(s) untracked bloqueante(s)...")
        cleaned = cleanup_untracked_gz()
        actions.append({
            "name": "Cleanup git untracked",
            "success": len(cleaned) > 0,
            "result": f"eliminados: {cleaned}" if cleaned else "nada eliminado (archivos no están en origin)",
        })
    else:
        log("Paso 1: sin archivos untracked bloqueantes.")

    # --- Paso 2: git pull ---
    log("Paso 2: intentando git pull...")
    pull_ok, pull_msg = attempt_git_pull()
    actions.append({
        "name": "git pull",
        "success": pull_ok,
        "result": pull_msg[:200],
    })
    metric("watchdog_pull_attempt", f"success={pull_ok} msg={pull_msg[:100]}")

    # --- Paso 3: reintentar update_products si el pull fue OK ---
    if pull_ok:
        log("Paso 3: ejecutando update_products.py...")
        update_ok, update_out = attempt_update_products()
        actions.append({
            "name": "update_products.py",
            "success": update_ok,
            "result": update_out[:300],
        })
        metric("watchdog_update_attempt", f"success={update_ok} out={update_out[:100]}")
    else:
        log("Paso 3: skipping update_products (pull falló).")
        actions.append({
            "name": "update_products.py",
            "success": False,
            "result": "skip — pull falló primero",
        })

    # --- Reporte final a Telegram ---
    report = build_telegram_report(actions, diag)
    log("Enviando reporte a Telegram...")
    sent = send_telegram(report)
    metric(
        "watchdog_done",
        f"actions={len(actions)} any_success={any(a['success'] for a in actions)} telegram={sent}"
    )

    any_success = any(a.get("success") for a in actions)
    return 0 if any_success else 1


if __name__ == "__main__":
    sys.exit(main())

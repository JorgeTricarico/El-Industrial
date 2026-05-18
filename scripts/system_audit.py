#!/usr/bin/env python3
"""Auditoria sistemica semanal.

Corre 1x/semana via cron (GH Actions) y manda un reporte al admin con todo
lo que se acumula silenciosamente. Los items que esto detecta son los que
ya nos mordieron:

  - cmd=hugo residual en build_settings de Netlify -> 19 dias congelado
  - SAMBANOVA_API_KEY faltante -> cadena LLM degradada sin nadie enterado
  - Pi/Mint offline > N dias -> dead-man-switch ya cubre 24h, esto el largo plazo
  - data/archive con .gz de hace 6 meses ocupando GB de la Pi
  - Workflows GH fallando en racha sin que nadie revise el dashboard

Por contrato (CLAUDE.md regla #1) NO se importan efectos externos al
toplevel del modulo: send_alert vive en una funcion separada y los tests
la mockean via conftest.py.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
TENANTS_DIR = os.path.join(BASE_DIR, "tenants")
REGISTRY = os.path.join(TENANTS_DIR, "_registry.yml")
STATUS_DIR = os.path.join(BASE_DIR, "status")
ARCHIVE_DIR = os.path.join(BASE_DIR, "data", "archive")
ENV_PATH = os.path.join(BASE_DIR, ".env")

# Umbrales (configurables via env si hace falta tunear)
NODE_OFFLINE_DAYS = int(os.getenv("AUDIT_NODE_OFFLINE_DAYS", "7"))
ARCHIVE_STALE_DAYS = int(os.getenv("AUDIT_ARCHIVE_STALE_DAYS", "90"))
WORKFLOW_FAIL_STREAK = int(os.getenv("AUDIT_WORKFLOW_FAIL_STREAK", "3"))
TENANT_DEPLOY_STALE_HOURS = int(os.getenv("AUDIT_TENANT_DEPLOY_STALE_HOURS", "48"))

# Claves que cada supplier necesita en .env (raiz por ahora; en Fase 2B,
# por tenant). Mantener sincronizado con scripts/suppliers/.
SUPPLIER_REQUIRED_KEYS = {
    "Bertual": ("BERTUAL_CUIT", "BERTUAL_PASSWORD", "BERTUAL_CLIENT_ID"),
    "Electronica Haedo": (),  # stub, no API
}

# Claves globales que el sistema entero necesita
GLOBAL_REQUIRED_KEYS = (
    "TELEGRAM_TOKEN",
    "NETLIFY_AUTH_TOKEN",
)

# Claves opcionales pero importantes (cadena LLM): se chequean separado
LLM_KEYS = ("GEMINI_API_KEY", "CEREBRAS_API_KEY", "SAMBANOVA_API_KEY")


def _read_env_keys(path=None):
    """Devuelve set de claves definidas (no vacias) en .env. {} si no existe."""
    path = path or ENV_PATH
    if not os.path.exists(path):
        return set()
    keys = set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v:
                    keys.add(k)
    except OSError:
        pass
    return keys


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


def check_tenants_deploys(tenants):
    """Para cada tenant active/testing, verifica que el .gz mas reciente
    sea fresco. Reporta tenants con el ultimo dataset > N horas."""
    problems = []
    now = datetime.now()
    for t in tenants:
        slug = t.get("slug", "?")
        if t.get("state") not in ("active", "testing"):
            continue
        data_dir = os.path.join(TENANTS_DIR, slug, "data")
        if not os.path.isdir(data_dir):
            problems.append(f"tenant '{slug}': falta carpeta data/")
            continue
        gz = sorted([f for f in os.listdir(data_dir) if f.endswith(".gz")], reverse=True)
        if not gz:
            problems.append(f"tenant '{slug}': sin .gz en data/")
            continue
        path = os.path.join(data_dir, gz[0])
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        age_h = (now - mtime).total_seconds() / 3600
        if age_h > TENANT_DEPLOY_STALE_HOURS:
            problems.append(
                f"tenant '{slug}': ultimo .gz tiene {age_h:.0f}h "
                f"(umbral {TENANT_DEPLOY_STALE_HOURS}h). Cron no esta corriendo o sync_tenants no espeja."
            )
    return problems


def check_env_keys(tenants):
    """Reporta claves criticas faltantes en .env raiz."""
    problems = []
    keys = _read_env_keys()

    for k in GLOBAL_REQUIRED_KEYS:
        if k not in keys:
            problems.append(f".env raiz: falta {k} (clave global requerida)")

    # Cadena LLM: si faltan 2+ de las 3, alerta. Si falta 1 sola, no es critico
    # porque hay fallback, pero lo mencionamos como warning.
    missing_llm = [k for k in LLM_KEYS if k not in keys]
    if len(missing_llm) >= 2:
        problems.append(
            f".env raiz: cadena LLM degradada — faltan {len(missing_llm)}/3 keys: "
            f"{', '.join(missing_llm)}"
        )

    # Claves por supplier de tenants activos
    for t in tenants:
        if t.get("state") != "active":
            continue
        supplier = t.get("supplier")
        required = SUPPLIER_REQUIRED_KEYS.get(supplier, ())
        for k in required:
            if k not in keys:
                problems.append(
                    f"tenant '{t.get('slug')}' (supplier {supplier}): falta {k} en .env"
                )
    return problems


def check_node_heartbeats():
    """Itera nodos en status/heartbeat.json y alerta de cada nodo cuyo
    last_run sea > N dias atras. Schema multi-nodo (M4, 2026-05-17).
    """
    problems = []
    sys.path.insert(0, SCRIPT_DIR)
    try:
        import heartbeat_io
    finally:
        if SCRIPT_DIR in sys.path:
            sys.path.remove(SCRIPT_DIR)
    hb = heartbeat_io.read(STATUS_DIR)
    nodes = hb.get("nodes", {})
    if not nodes:
        return ["status/heartbeat.json sin nodos — ningun nodo reporto aun"]
    for node, entry in nodes.items():
        last = entry.get("last_run")
        if not last:
            problems.append(f"heartbeat sin campo last_run (nodo {node})")
            continue
        try:
            dt = datetime.fromisoformat(last)
        except ValueError:
            problems.append(f"heartbeat last_run no parseable: {last!r} (nodo {node})")
            continue
        days = (datetime.now() - dt).days
        if days > NODE_OFFLINE_DAYS:
            problems.append(
                f"heartbeat: ultimo run nodo {node} hace {days}d "
                f"(umbral {NODE_OFFLINE_DAYS}d). Posible nodo caido."
            )
    return problems


def check_archive_stale():
    """Reporta archivos en data/archive/ mas viejos que N dias."""
    if not os.path.isdir(ARCHIVE_DIR):
        return []
    problems = []
    cutoff = datetime.now() - timedelta(days=ARCHIVE_STALE_DAYS)
    stale = []
    for fname in os.listdir(ARCHIVE_DIR):
        path = os.path.join(ARCHIVE_DIR, fname)
        if not os.path.isfile(path):
            continue
        mtime = datetime.fromtimestamp(os.path.getmtime(path))
        if mtime < cutoff:
            stale.append(fname)
    if stale:
        problems.append(
            f"data/archive/: {len(stale)} archivo(s) > {ARCHIVE_STALE_DAYS}d. "
            f"Ejemplo: {stale[0]}. Considerar limpieza."
        )
    return problems


def check_workflow_failures(repo=None, token=None, streak=WORKFLOW_FAIL_STREAK):
    """Llama a GH API para los ultimos N runs de cada workflow. Reporta
    workflows con racha de fallos >= streak. Requiere GITHUB_TOKEN.

    repo: 'owner/name'. Si None, lee de env GITHUB_REPOSITORY.
    """
    repo = repo or os.getenv("GITHUB_REPOSITORY")
    token = token or os.getenv("GITHUB_TOKEN")
    if not repo or not token:
        return []  # No tenemos credenciales -> skip silencioso (no es problema del sistema)
    try:
        import requests
    except ImportError:
        return []
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    problems = []
    try:
        wf_res = requests.get(
            f"https://api.github.com/repos/{repo}/actions/workflows",
            headers=headers, timeout=15,
        )
        if not wf_res.ok:
            return [f"GH API workflows: HTTP {wf_res.status_code}"]
        workflows = wf_res.json().get("workflows", [])
    except Exception as e:
        return [f"GH API error workflows: {type(e).__name__}"]

    for wf in workflows:
        wf_id = wf.get("id")
        wf_name = wf.get("name", "?")
        try:
            runs_res = requests.get(
                f"https://api.github.com/repos/{repo}/actions/workflows/{wf_id}/runs",
                headers=headers, params={"per_page": streak}, timeout=15,
            )
            runs = runs_res.json().get("workflow_runs", []) if runs_res.ok else []
        except Exception:
            continue
        if len(runs) < streak:
            continue
        if all(r.get("conclusion") == "failure" for r in runs[:streak]):
            problems.append(
                f"workflow '{wf_name}': {streak} runs consecutivos en failure. "
                f"Revisar Actions tab."
            )
    return problems


def check_netlify_build_settings(tenants, token=None):
    """Para cada tenant, lee build_settings de su site y verifica que
    cmd este vacio y dir apunte a tenants/<slug>. Atrapa el patron del
    cmd=hugo residual.

    Requiere NETLIFY_AUTH_TOKEN.
    """
    token = token or os.getenv("NETLIFY_AUTH_TOKEN")
    if not token:
        return [".env raiz: NETLIFY_AUTH_TOKEN ausente — no se puede auditar build_settings"]
    try:
        import requests
    except ImportError:
        return []
    problems = []
    headers = {"Authorization": f"Bearer {token}"}
    for t in tenants:
        slug = t.get("slug", "?")
        site_id = t.get("netlify_site_id")
        if not site_id:
            continue
        try:
            r = requests.get(
                f"https://api.netlify.com/api/v1/sites/{site_id}",
                headers=headers, timeout=15,
            )
        except Exception as e:
            problems.append(f"tenant '{slug}': Netlify API error ({type(e).__name__})")
            continue
        if not r.ok:
            problems.append(f"tenant '{slug}': Netlify GET site HTTP {r.status_code}")
            continue
        site = r.json()
        bs = site.get("build_settings") or {}
        cmd = (bs.get("cmd") or "").strip()
        pub_dir = (bs.get("dir") or "").strip()
        expected_dir = f"tenants/{slug}"
        if cmd:
            problems.append(
                f"tenant '{slug}': build_settings.cmd={cmd!r} (esperado vacio). "
                f"Riesgo: bug del 19 dias se repite si autodeploy se reactiva."
            )
        if pub_dir and pub_dir != expected_dir:
            problems.append(
                f"tenant '{slug}': build_settings.dir={pub_dir!r} "
                f"(esperado {expected_dir!r})"
            )
        if not site.get("build_settings", {}).get("stop_builds", True):
            # stop_builds=False significa que autodeploy git esta activo
            problems.append(
                f"tenant '{slug}': stop_builds=False (autodeploy git activo, "
                f"deberia estar desactivado: deploy via API solo)."
            )
    return problems


LOG_SIZE_WARN_MB = float(os.getenv("LOG_SIZE_WARN_MB", "100"))


def check_log_sizes():
    """Reporta archivos append-only que crecieron por encima del umbral.

    Si rotate_all() esta enganchado en nightly_report y aun asi los archivos
    siguen creciendo, hay algo pasando (cron loopeando, error de write).
    """
    problems = []
    targets = [
        ("status/metrics.jsonl", os.path.join(STATUS_DIR, "metrics.jsonl")),
        ("reports/cron_log.txt", os.path.join(BASE_DIR, "reports", "cron_log.txt")),
    ]
    for label, path in targets:
        if not os.path.exists(path):
            continue
        size_mb = os.path.getsize(path) / (1024 * 1024)
        if size_mb > LOG_SIZE_WARN_MB:
            problems.append(
                f"{label}: {size_mb:.1f}MB (umbral {LOG_SIZE_WARN_MB:.0f}MB). "
                "Verificar rotacion en nightly_report (log_rotation.rotate_all)."
            )
    return problems


def check_cluster_registry():
    """Cruza infra/nodes.yml con heartbeat.json y detecta:
    - Nodos declarados active sin pulso reciente.
    - Nodos con pulso pero NO declarados en nodes.yml (sin onboardear).
    """
    problems = []
    registry_path = os.path.join(BASE_DIR, "infra", "nodes.yml")
    if not os.path.exists(registry_path):
        return problems
    try:
        import yaml
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = yaml.safe_load(f) or {}
    except Exception as e:
        return [f"infra/nodes.yml ilegible: {e}"]

    sys.path.insert(0, SCRIPT_DIR)
    try:
        import heartbeat_io
    finally:
        if SCRIPT_DIR in sys.path:
            sys.path.remove(SCRIPT_DIR)
    hb = heartbeat_io.read(STATUS_DIR)
    pulsed_hosts = set(hb.get("nodes", {}).keys())
    declared = {n["hostname"]: n for n in registry.get("nodes", []) if n.get("hostname")}

    # Nodos active sin pulso reciente
    for hostname, node in declared.items():
        if node.get("state") != "active":
            continue
        if hostname == "github-actions":
            continue  # GH no escribe heartbeat por si mismo (corre en ubuntu-latest efimero)
        entry = hb.get("nodes", {}).get(hostname)
        if not entry:
            problems.append(
                f"nodo declarado active '{hostname}' nunca pulso en heartbeat. "
                f"Verificar cron / venv / push permissions."
            )
            continue
        last = entry.get("last_run")
        if not last:
            problems.append(f"nodo '{hostname}' sin campo last_run en heartbeat.")
            continue
        try:
            dt = datetime.fromisoformat(last)
            hours_ago = (datetime.now() - dt).total_seconds() / 3600
            if hours_ago > 36:
                problems.append(
                    f"nodo active '{hostname}' sin pulso hace {hours_ago:.1f}h "
                    f"(role={node.get('role')}, cron={node.get('cron')!r}). "
                    f"Posible cron roto o nodo caido."
                )
        except ValueError:
            problems.append(f"nodo '{hostname}': last_run no parseable: {last!r}")

    # Nodos con pulso pero no declarados (sin onboardear)
    undeclared = pulsed_hosts - set(declared.keys())
    for hostname in undeclared:
        problems.append(
            f"nodo '{hostname}' pulsa pero NO esta en infra/nodes.yml. "
            f"Onboardearlo (state, role, cron, location)."
        )
    return problems


def run_audit():
    """Corre todas las checks. Retorna (sections, total_problems).
    sections es dict {section_name: [problemas]}. total_problems es int.
    """
    tenants = load_tenants()
    sections = {
        "Tenants (deploys frescos)": check_tenants_deploys(tenants),
        ".env (keys requeridas)": check_env_keys(tenants),
        "Nodos (heartbeat reciente)": check_node_heartbeats(),
        "Cluster (registry vs pulso)": check_cluster_registry(),
        "Archivos viejos (data/archive)": check_archive_stale(),
        "Logs append-only (tamano)": check_log_sizes(),
        "Workflows GH (rachas de fallos)": check_workflow_failures(),
        "Netlify build_settings": check_netlify_build_settings(tenants),
    }
    total = sum(len(v) for v in sections.values())
    return sections, total


def format_report(sections, total):
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    if total == 0:
        return (
            f"🟢 <b>Audit semanal — {now} AR</b>\n\n"
            f"Sin observaciones. Todos los chequeos OK.\n"
            f"<i>(audit detalla: tenants, .env keys, nodos, archive, workflows, Netlify build_settings)</i>"
        )
    body = f"🟡 <b>Audit semanal — {now} AR</b> <i>(solo dev)</i>\n\n"
    body += f"<b>{total} observacion(es):</b>\n\n"
    for section, problems in sections.items():
        if not problems:
            continue
        body += f"<b>{section}</b>\n"
        for p in problems:
            body += f"• {p}\n"
        body += "\n"
    body += "<i>Detalle: scripts/system_audit.py. No urgente — revisar en la semana.</i>"
    return body


def send_alert(body):
    """Manda el reporte al admin via Telegram. NO-OP si no hay token.
    Por contrato del repo (CLAUDE.md regla #1) los tests sobreescriben
    esta funcion via conftest.py para evitar envios reales."""
    sys.path.insert(0, SCRIPT_DIR)
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH)
    except ImportError:
        pass
    token = os.getenv("TELEGRAM_TOKEN")
    legacy = os.getenv("TELEGRAM_CHAT_ID")
    if not token:
        print("[audit] TELEGRAM_TOKEN ausente, skip envio.", file=sys.stderr)
        return False
    try:
        import clients as _c
        import requests
    except ImportError:
        return False
    recipients = _c.recipients_for("alert", legacy_chat_id=legacy)
    if not recipients:
        print("[audit] sin destinatarios admin.", file=sys.stderr)
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    sent = 0
    for chat_id, _name in recipients:
        try:
            r = requests.post(
                url, data={"chat_id": chat_id, "text": body, "parse_mode": "HTML"}, timeout=15,
            )
            if r.ok:
                sent += 1
        except Exception:
            pass
    return sent > 0


def main():
    sections, total = run_audit()
    body = format_report(sections, total)
    print(body)
    send_alert(body)
    # Audit es informativo: no fallamos el workflow para no spammear.
    return 0


if __name__ == "__main__":
    sys.exit(main())

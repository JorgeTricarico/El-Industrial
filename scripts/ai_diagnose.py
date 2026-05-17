#!/usr/bin/env python3
"""Diagnostico AI con contexto del sistema cuando hay alertas internas.

Cuando post_deploy_check o healthcheck detectan un fallo, NO tiene que
llegarle al cliente final — solo al dev. Pero el dev quiere un informe
con causa raiz analizada por un LLM, no solo la lista de problemas.

Este modulo:
  1. Junta contexto del sistema (cron_log, metrics, git HEAD, tenants).
  2. Pasa todo + los problems al modelo via cadena de fallback
     (Gemini -> Cerebras -> SambaNova -> plantilla deterministica).
  3. Devuelve un texto corto en HTML listo para Telegram.

Esta separado de nightly_report.py para no acoplar: el reporte comercial
tiene tono "vendedor amigo PYME"; este informe tiene tono SRE/tecnico.
"""
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
STATUS_DIR = os.path.join(BASE_DIR, "status")

sys.path.insert(0, SCRIPT_DIR)


def collect_context(max_log_lines=15, max_metrics=10):
    """Devuelve un dict con snapshot del sistema relevante al diagnostico."""
    ctx = {}
    # Git HEAD + branch
    try:
        ctx["git_head"] = subprocess.check_output(
            ["git", "-C", BASE_DIR, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        ctx["git_head"] = "unknown"
    # Heartbeat
    hb_path = os.path.join(STATUS_DIR, "heartbeat.json")
    if os.path.exists(hb_path):
        try:
            with open(hb_path, "r", encoding="utf-8") as f:
                ctx["heartbeat"] = json.load(f)
        except Exception:
            ctx["heartbeat"] = None
    # Cron log tail
    log_path = os.path.join(BASE_DIR, "reports", "cron_log.txt")
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                ctx["cron_log_tail"] = "".join(f.readlines()[-max_log_lines:])
        except Exception:
            ctx["cron_log_tail"] = ""
    # Metrics tail (ultimas N entradas)
    metrics_path = os.path.join(STATUS_DIR, "metrics.jsonl")
    if os.path.exists(metrics_path):
        try:
            with open(metrics_path, "r", encoding="utf-8") as f:
                lines = f.readlines()[-max_metrics:]
                ctx["metrics_tail"] = [json.loads(l) for l in lines if l.strip()]
        except Exception:
            ctx["metrics_tail"] = []
    # Tenants registrados
    reg = os.path.join(BASE_DIR, "tenants", "_registry.yml")
    if os.path.exists(reg):
        try:
            import yaml
            with open(reg, "r", encoding="utf-8") as f:
                d = yaml.safe_load(f) or {}
            ctx["tenants"] = [
                {"slug": t.get("slug"), "state": t.get("state"), "supplier": t.get("supplier")}
                for t in d.get("tenants", [])
            ]
        except Exception:
            ctx["tenants"] = []
    return ctx


def build_prompt(problems, context):
    """Prompt SRE/diagnostico, tono tecnico y conciso para admin/dev."""
    ctx_compact = json.dumps(context, ensure_ascii=False, indent=2)[:3000]
    problems_block = "\n".join(f"- {p}" for p in problems)
    return f"""Sos un SRE diagnosticando un sistema de monitoreo de precios multi-tenant.
Te paso (a) la lista de problemas detectados ahora, (b) snapshot del sistema.

STACK REAL (no inventes infra que no esta aca):
- Python scripts en repo git, NO hay Docker, NO hay Kubernetes, NO hay systemd-units propias.
- Cron en Raspberry Pi (Tailscale 100.112.235.98) corriendo `scripts/update_products.py`,
  `scripts/nightly_report.py`, `scripts/healthcheck.py`, `scripts/post_deploy_check.py`.
- Fallback en GitHub Actions si la Pi se cae.
- Logs: `reports/cron_log.txt` (tail del cron), `status/metrics.jsonl`, `status/alerts.jsonl`.
- Deploy a Netlify via API REST desde `scripts/sync_tenants.py` (autodeploy git desactivado).
- Suppliers en `scripts/suppliers/` (Bertual, Electronica Haedo). Cada tenant en `tenants/<slug>/`.

PROBLEMAS DETECTADOS:
{problems_block}

CONTEXTO ACTUAL DEL SISTEMA:
{ctx_compact}

Tu respuesta DEBE ser HTML simple para Telegram (solo <b>negrita</b> y bullets con "• "),
maximo 600 caracteres, en este formato:

<b>Causa raiz probable:</b> 1 linea.
<b>Severidad:</b> baja/media/alta (con justificacion en 1 frase).
<b>Accion sugerida:</b> 1-2 bullets concretos. Si sugeris un comando, tiene que ser EJECUTABLE
en este stack (ssh a la Pi, `tail reports/cron_log.txt`, `python scripts/<algo>.py`, `git log`).
PROHIBIDO sugerir: docker, kubectl, journalctl de servicios inexistentes, nombres de servicios
que no aparezcan en el contexto.

No saludes. No te disculpes. No repitas los problems. No inventes detalles.
Si la info no alcanza para diagnosticar, decilo en una linea.
"""


def diagnose(problems, context=None):
    """Llama la cadena LLM y devuelve (analysis_html, provider).

    Si los 3 LLMs caen, retorna (template_analysis, "template").
    """
    if not problems:
        return ("", "noop")
    if context is None:
        context = collect_context()
    prompt = build_prompt(problems, context)
    try:
        import nightly_report as nr  # reusa la cadena Gemini->Cerebras->SambaNova
    except ImportError:
        return (_template_analysis(problems), "template")
    text, provider = nr.get_ai_analysis(prompt)
    if text:
        try:
            text = nr.sanitize_html(text)
        except Exception:
            pass
        return (text, provider)
    return (_template_analysis(problems), "template")


def _template_analysis(problems):
    """Fallback determinista si todos los LLMs caen."""
    n = len(problems)
    return (
        f"<b>Causa raiz probable:</b> no analizable (LLMs caidos).\n"
        f"<b>Severidad:</b> indeterminada — {n} problema(s) detectado(s).\n"
        f"<b>Accion sugerida:</b> revisar manualmente reports/cron_log.txt y status/metrics.jsonl."
    )


if __name__ == "__main__":
    # Uso CLI: echo problem | python ai_diagnose.py  o  python ai_diagnose.py "prob1" "prob2"
    args = sys.argv[1:]
    if not args:
        args = [line.strip() for line in sys.stdin if line.strip()]
    text, provider = diagnose(args)
    print(f"[provider={provider}]")
    print(text)

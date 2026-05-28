#!/usr/bin/env python3
"""AIOps Remediation Agent.

Este script es invocado asincronamente por _send_tech_alert cuando ocurre un error
critico en la infraestructura (ej. caida de API, problemas de cron).
Toma el error, solicita a la IA que analice y sugiera una correccion, y
- Envia un reporte de diagnostico por Telegram al admin.
- (Opcionalmente) crea un Issue en GitHub.
"""
import os
import sys
import json
import time
import requests
import subprocess
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
STATUS_DIR = os.path.join(BASE_DIR, "status")
ENV_FILE = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_FILE)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

def log_metric(event, detail=""):
    os.makedirs(STATUS_DIR, exist_ok=True)
    from datetime import datetime
    entry = {"ts": datetime.now().isoformat(), "node": "aiops", "event": event, "detail": detail[:500]}
    try:
        with open(os.path.join(STATUS_DIR, "metrics.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=15)
    except requests.RequestException:
        pass

def call_llm_chain(prompt):
    import nightly_report
    body, provider = nightly_report.get_ai_analysis(prompt)
    return body

def fetch_recent_logs():
    """Obtiene las ultimas lineas de metrics.jsonl como contexto."""
    log_path = os.path.join(STATUS_DIR, "metrics.jsonl")
    if not os.path.exists(log_path):
        return "No logs available."
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            return "".join(lines[-10:])
    except Exception as e:
        return f"Error reading logs: {e}"

def create_github_issue(title, body):
    """Crea un issue en github usando gh CLI si esta disponible."""
    try:
        res = subprocess.run(["gh", "issue", "create", "--title", title, "--body", body], cwd=BASE_DIR, capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            return True, res.stdout.strip()
        else:
            return False, res.stderr.strip()
    except Exception as e:
        return False, str(e)

def main():
    if len(sys.argv) < 2:
        return
    error_msg = sys.argv[1]
    
    recent_logs = fetch_recent_logs()
    
    prompt = f"""Sos el Agente AIOps desatendido del sistema 'El Industrial'.
Se ha detectado la siguiente alerta tecnica en produccion:
"{error_msg}"

Contexto reciente de logs (metrics.jsonl):
{recent_logs}

Tu tarea es:
1. Analizar el error.
2. Dar un diagnostico rapido de que pudo haber fallado.
3. Proponer los pasos de accion concretos (Remediacion).

Devolve tu respuesta en HTML simple (para Telegram), usando etiquetas <b> para destacar.
Empeza con: "🤖 <b>AIOps Auto-Diagnostico</b>"."""

    analysis = call_llm_chain(prompt)
    if analysis:
        import nightly_report
        analysis_safe = nightly_report.sanitize_html(analysis)
        send_telegram(analysis_safe)
        log_metric("aiops_success", "Analisis generado y enviado.")
        
        # Opcion A: Crear Issue
        issue_title = f"AIOps Alert: {error_msg[:50]}..."
        issue_body = f"**AIOps Auto-Diagnostico**\n\n```html\n{analysis}\n```"
        success, gh_out = create_github_issue(issue_title, issue_body)
        if success:
            send_telegram(f"ℹ️ Issue creado automaticamente: {gh_out}")
            log_metric("aiops_issue_created", gh_out)
        else:
            log_metric("aiops_issue_failed", gh_out)
            # Fallback local de incidentes
            try:
                incidents_dir = os.path.join(STATUS_DIR, "incidents")
                os.makedirs(incidents_dir, exist_ok=True)
                import time
                inc_path = os.path.join(incidents_dir, f"incident_{int(time.time())}.json")
                with open(inc_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "title": issue_title,
                        "error_msg": error_msg,
                        "analysis": analysis,
                        "gh_error": gh_out
                    }, f, indent=2, ensure_ascii=False)
                log_metric("aiops_incident_archived", os.path.basename(inc_path))
            except Exception as e:
                log_metric("aiops_incident_archive_failed", str(e))
    else:
        log_metric("aiops_failed", "No se pudo obtener analisis del LLM")

if __name__ == "__main__":
    main()

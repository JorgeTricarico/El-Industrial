#!/usr/bin/env python3
"""Mensaje Telegram tecnico cuando GitHub Actions detecta que la Pi no respondio.

Se invoca desde fallback_sync.yml y failover.yml.
Ahora es un mensaje exclusivamente tecnico (Alerta) enviado al dev,
no al cliente final.
"""
import os
import sys
from datetime import datetime
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)

def send_alert(text):
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(BASE_DIR, ".env"))
    except ImportError:
        pass
        
    token = os.getenv("TELEGRAM_TOKEN")
    legacy = os.getenv("TELEGRAM_CHAT_ID")
    if not token:
        print("[telegram] credenciales ausentes", file=sys.stderr)
        return False
        
    try:
        import clients as _c
    except ImportError:
        print("[telegram] modulo clients no encontrado", file=sys.stderr)
        return False

    recipients = _c.recipients_for("alert", legacy_chat_id=legacy)
    if not recipients:
        print("[telegram] sin destinatarios admin", file=sys.stderr)
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    sent = 0
    for chat_id, _name in recipients:
        try:
            res = requests.post(
                url,
                data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=15,
            )
            if res.ok:
                sent += 1
        except requests.RequestException as e:
            print(f"[telegram] fallo: {e}", file=sys.stderr)
            
    return sent > 0

def main():
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    msg = (
        f"🚨 <b>Alerta Técnica: Nodo Local Offline</b>\n\n"
        f"Fecha: {fecha}\n"
        "GitHub Actions detectó que el nodo principal no reportó a tiempo "
        "y ejecutó la rutina de fallback.\n\n"
        "No se enviarán notificaciones al cliente comercial."
    )
    ok = send_alert(msg)
    print(f"telegram sent: {ok}")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Mensaje Telegram especifico cuando GitHub Actions detecta que la Pi no respondio.

Se invoca desde fallback_sync.yml y failover.yml en lugar de intentar
update_products.py (que falla por timeout ya que la API Bertual no es accesible
desde los runners de GitHub Actions).

Idea: avisar al cliente que hoy NO hay lista nueva, sin pretender disimular.
"""
import os
import sys
from datetime import datetime
import requests
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
load_dotenv(os.path.join(BASE_DIR, ".env"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send(text):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        print("[telegram] credenciales ausentes", file=sys.stderr)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        res = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        return res.ok
    except requests.RequestException as e:
        print(f"[telegram] fallo: {e}", file=sys.stderr)
        return False


def main():
    fecha = datetime.now().strftime("%d/%m/%Y")
    msg = (
        f"📍 <b>Aviso del dia — {fecha}</b>\n\n"
        "El nodo local no respondio en su horario habitual.\n"
        "No hay lista de precios nueva hoy.\n\n"
        "Mañana se reintenta automaticamente. "
        "Si esto se repite varios dias, avisanos."
    )
    ok = send(msg)
    print(f"telegram sent: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

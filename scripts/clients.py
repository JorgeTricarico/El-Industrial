"""Lectura del archivo config/clients.yml — lista de destinatarios de Telegram.

Centraliza la logica para que nightly_report y healthcheck reciban los chat_ids
correctos segun la categoria del mensaje (reporte comercial vs alerta tecnica).
"""
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
CONFIG_PATH = os.path.join(BASE_DIR, "config", "clients.yml")

_VALID_ROLES = ("admin", "client")


def load_clients(path=None):
    """Devuelve la lista cruda de clientes del YAML. [] si no hay archivo o falla."""
    path = path or CONFIG_PATH
    if not os.path.exists(path):
        return []
    try:
        import yaml
    except ImportError:
        print("[clients] pyyaml no instalado, ignorando clients.yml", file=sys.stderr)
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        print(f"[clients] error leyendo {path}: {e}", file=sys.stderr)
        return []
    clients = data.get("clients", [])
    if not isinstance(clients, list):
        return []
    return clients


def recipients_for(category, legacy_chat_id=None, path=None):
    """Devuelve la lista de chat_ids que deben recibir un mensaje de la categoria dada.

    category:
        "report" -> reporte comercial nocturno. Va a roles admin + client.
        "alert"  -> alerta tecnica de healthcheck. Va SOLO a role admin.

    legacy_chat_id: si no hay clients.yml (o esta vacio), se usa este chat_id
                    como unico destinatario. Pasale el TELEGRAM_CHAT_ID del .env
                    desde el script que llame.

    Override por env (separa canales tecnicos de comerciales):
        TELEGRAM_TECH_CHAT_ID  — si esta seteado, REEMPLAZA los destinatarios
                                 cuando category=="alert". Util para mandar
                                 alertas a un chat dev separado del cliente.

    Retorna lista de (chat_id:str, name:str). Vacia si no hay destinatarios validos.
    """
    if category not in ("report", "alert"):
        raise ValueError(f"category invalida: {category!r}")

    # Override por env: si se definio canal tecnico separado, las alertas van
    # SOLO ahi y no a los admin chats de clients.yml. Esto permite separar
    # el chat del cliente final del chat de dev/ops cuando se sumen clientes pagos.
    if category == "alert":
        tech_chat = os.getenv("TELEGRAM_TECH_CHAT_ID", "").strip()
        if tech_chat:
            return [(tech_chat, "tech_channel")]

    raw = load_clients(path=path)
    out = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        if not c.get("enabled"):
            continue
        role = c.get("role", "")
        if role not in _VALID_ROLES:
            continue
        if category == "alert" and role != "admin":
            continue
        chat_id = c.get("telegram_chat_id")
        if not chat_id:
            continue
        out.append((str(chat_id), c.get("name", "?")))

    # Fallback legacy: si no hay clients.yml o esta sin entradas habilitadas,
    # usar el TELEGRAM_CHAT_ID viejo del .env para no romper instalaciones existentes.
    if not out and legacy_chat_id:
        out.append((str(legacy_chat_id), "legacy_env_chat_id"))

    return out

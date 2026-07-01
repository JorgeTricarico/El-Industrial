#!/usr/bin/env python3
"""Pulso del nodo: SIEMPRE escribe al heartbeat aunque la corrida no haya
hecho nada util (Bertual abajo, dup_skip de otro nodo, push fail, etc).

Asi tenemos trazabilidad total del cluster: cada device deja huella en cada
corrida del cron. system_audit cruza esto contra infra/nodes.yml y detecta
nodos caidos.

Uso:
    node_pulse.py [--outcome <str>] [--note <str>]

Llamado desde run_daily.sh al arranque y al final, con outcome distinto.
"""
import argparse
import os
import socket
import subprocess
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
STATUS_DIR = os.path.join(BASE_DIR, "status")

sys.path.insert(0, SCRIPT_DIR)
import heartbeat_io  # noqa: E402


def short_head():
    try:
        return subprocess.check_output(
            ["git", "-C", BASE_DIR, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unknown"


def declared_role(hostname):
    """Lee infra/nodes.yml y devuelve el rol declarado del nodo, o 'unknown'."""
    try:
        import yaml
        path = os.path.join(BASE_DIR, "infra", "nodes.yml")
        if not os.path.exists(path):
            return "unknown"
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for n in data.get("nodes", []):
            if n.get("hostname") == hostname:
                return n.get("role", "unknown")
    except Exception:
        pass
    return "unknown"


def effective_role(hostname, env_role=None):
    """Resuelve el rol OPERATIVO del nodo: "primary" o "backup".

    Es la fuente de verdad unica que consume run_daily.sh (via --resolve-role)
    para decidir si el nodo pushea precios reales. Prioridad:

      1. env_role explicito (EL_INDUSTRIAL_ROLE) si es valido.
      2. Rol declarado en infra/nodes.yml para este hostname:
         primary -> primary; backup|dev|cloud_last_resort -> backup.
      3. Fallback legacy (host no registrado): hostname con "mint" -> backup;
         cualquier otro -> primary.

    El bug que motiva esto (2026-07-01): DESKTOP-MI43BOU esta declarado como
    backup en nodes.yml pero el fallback legacy elegia primary para todo host
    que no dijera "mint" — asi un backup mal registrado se auto-elegia primary
    y le pegaba a Bertual de madrugada generando ruido supplier_down.
    """
    if env_role:
        er = env_role.strip().lower()
        if er in ("primary", "backup", "dev"):
            return "primary" if er == "primary" else "backup"
    declared = declared_role(hostname)
    if declared == "primary":
        return "primary"
    if declared in ("backup", "dev", "cloud_last_resort"):
        return "backup"
    # Host no registrado en nodes.yml: fallback legacy por hostname.
    return "backup" if "mint" in hostname.lower() else "primary"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outcome", default="started",
                        help="started|updated|dup_skip|supplier_fail|push_fail|finished")
    parser.add_argument("--note", default="", help="Detalle libre (max 200 chars)")
    parser.add_argument("--resolve-role", action="store_true",
                        help="Imprime el rol operativo (primary|backup) y sale, sin tocar heartbeat.")
    args = parser.parse_args(argv)

    hostname = socket.gethostname()

    if args.resolve_role:
        print(effective_role(hostname, os.environ.get("EL_INDUSTRIAL_ROLE")))
        return 0

    fields = {
        "last_run": datetime.now().isoformat(),
        "last_outcome": args.outcome[:40],
        "version": short_head(),
        "role_declared": declared_role(hostname),
    }
    if args.note:
        fields["note"] = args.note[:200]

    try:
        heartbeat_io.write_node(STATUS_DIR, hostname, fields)
        print(f"[node_pulse] {hostname} outcome={args.outcome} version={fields['version']}")
        return 0
    except Exception as e:
        print(f"[node_pulse] ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

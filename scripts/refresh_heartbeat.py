#!/usr/bin/env python3
"""Refresca el campo `version` del heartbeat al HEAD git actual.

Se invoca despues de cada `git pull --rebase` en run_daily.sh para que el
healthcheck no genere falsos positivos de "drift de version" entre el pull
y la proxima corrida del cron (que es la unica que normalmente actualizaba
el heartbeat via update_products.update_heartbeat).

NO toca status/duration_s/last_run del heartbeat: esos solo los puede
modificar update_products en una corrida real. Solo agrega/actualiza:
  - version            (HEAD corto actual)
  - last_pulled_iso    (timestamp del ultimo pull exitoso)
"""
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


def main():
    head = short_head()
    node = socket.gethostname()
    heartbeat_io.write_node(STATUS_DIR, node, {
        "version": head,
        "last_pulled_iso": datetime.now().isoformat(),
    })
    print(f"[refresh_heartbeat] version={head}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

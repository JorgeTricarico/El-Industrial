#!/usr/bin/env python3
"""Rotacion de logs append-only (status/metrics.jsonl, reports/cron_log.txt).

Motivacion: la Pi tiene SD card; metrics.jsonl se appendea cada cron (cada
30 min) y cron_log.txt en cada corrida. Sin rotacion en 12 meses son cientos
de MB. Ya rotamos `status/archive/` (nightly_report.prune_old_archives) pero
estos dos archivos no se tocaban.

Politica:
- Si el archivo supera DEFAULT_MAX_MB (default 50MB, override via env
  `LOG_ROTATE_MAX_MB`), se mueve a `<archive_dir>/<basename>_<YYYY-MM>.gz`
  comprimido y se reinicia vacio.
- Si ya existe el archivo del mes en el archive, se appendea a el (concat
  gzipped). No es la opcion mas linda, pero evita perder data si se rotan
  varias veces en el mismo mes.
- Idempotente: no toca archivos por debajo del umbral.

Llamado desde:
- `nightly_report.main()` al final (1x/dia, asi no agrega latencia al cron
  frecuente cada 30 min).
- Manualmente: `python3 scripts/log_rotation.py`.
"""
import gzip
import io
import os
import shutil
import sys
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
STATUS_DIR = os.path.join(BASE_DIR, "status")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")

DEFAULT_MAX_MB = float(os.getenv("LOG_ROTATE_MAX_MB", "50"))

# Archivos que rotamos. (ruta_absoluta, dir_archive_destino).
def _default_targets():
    return [
        (os.path.join(STATUS_DIR, "metrics.jsonl"),
         os.path.join(STATUS_DIR, "archive")),
        (os.path.join(REPORTS_DIR, "cron_log.txt"),
         os.path.join(REPORTS_DIR, "archive")),
    ]


def _size_mb(path):
    try:
        return os.path.getsize(path) / (1024 * 1024)
    except OSError:
        return 0.0


def rotate_file(path, archive_dir, max_mb=DEFAULT_MAX_MB):
    """Si path supera max_mb, lo mueve a archive_dir comprimido y lo vacia.

    Retorna dict con info: {rotated: bool, size_mb, archive_path}.
    """
    info = {"rotated": False, "size_mb": _size_mb(path), "archive_path": None}
    if not os.path.exists(path):
        return info
    if info["size_mb"] <= max_mb:
        return info

    os.makedirs(archive_dir, exist_ok=True)
    ym = datetime.now().strftime("%Y-%m")
    basename = os.path.basename(path)
    archive_path = os.path.join(archive_dir, f"{basename}_{ym}.gz")

    # Si ya existe el archivo del mes: concatenamos. Leer el viejo,
    # appendear contenido nuevo, reescribir.
    try:
        existing = b""
        if os.path.exists(archive_path):
            with gzip.open(archive_path, "rb") as f:
                existing = f.read()
        with open(path, "rb") as f:
            new = f.read()
        buf = io.BytesIO()
        with gzip.open(buf, "wb") as gz:
            gz.write(existing)
            gz.write(new)
        with open(archive_path, "wb") as f:
            f.write(buf.getvalue())
        # Truncar el archivo original
        open(path, "w").close()
        info["rotated"] = True
        info["archive_path"] = archive_path
        info["size_mb"] = 0.0
    except OSError as e:
        print(f"[log_rotation] error rotando {path}: {e}", file=sys.stderr)
    return info


def rotate_all(max_mb=DEFAULT_MAX_MB, targets=None):
    """Rota todos los targets default. Retorna lista de infos."""
    if targets is None:
        targets = _default_targets()
    results = []
    for path, archive_dir in targets:
        res = rotate_file(path, archive_dir, max_mb=max_mb)
        res["path"] = path
        results.append(res)
        if res["rotated"]:
            print(f"[log_rotation] {path} -> {res['archive_path']}")
    return results


if __name__ == "__main__":
    rotate_all()

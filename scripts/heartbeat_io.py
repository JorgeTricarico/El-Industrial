#!/usr/bin/env python3
"""I/O del heartbeat multi-nodo. Centraliza el formato.

Schema actual (post M4, 2026-05-17):
{
  "nodes": {
    "raspberrypi":      {"last_run": "...", "status": "ok", "duration_s": 3.2,
                          "version": "abc1234", "last_pulled_iso": "..."},
    "DESKTOP-MI43BOU":  {...}
  },
  "last_telegram_iso": "...",
  "last_telegram_provider": "gemini"
}

Schema legacy (pre M4):
{
  "last_run": "...", "node": "raspberrypi", "status": "ok",
  "duration_s": 3.2, "version": "...", "last_telegram_iso": "...",
  "last_telegram_provider": "..."
}

`read()` normaliza ambos a la forma nueva. `write_node(...)` mergea sin
pisar otros nodos. `update_telegram(...)` actualiza el campo global.
"""
import json
import os

NODE_FIELDS = (
    "last_run", "status", "duration_s", "version", "last_pulled_iso",
)


def _hb_path(status_dir):
    return os.path.join(status_dir, "heartbeat.json")


def _normalize(raw):
    """Convierte legacy single-node a multi-node. Idempotente."""
    if not isinstance(raw, dict):
        return {"nodes": {}}
    if "nodes" in raw and isinstance(raw["nodes"], dict):
        return raw
    # Legacy: campos al top-level. Extraemos los de nodo a nodes[<name>].
    legacy_node = raw.get("node") or "unknown"
    node_entry = {k: raw[k] for k in NODE_FIELDS if k in raw}
    new = {"nodes": {legacy_node: node_entry}}
    for k in ("last_telegram_iso", "last_telegram_provider"):
        if k in raw:
            new[k] = raw[k]
    return new


def read(status_dir):
    """Devuelve dict normalizado al schema nuevo. Vacio si no existe."""
    path = _hb_path(status_dir)
    if not os.path.exists(path):
        return {"nodes": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _normalize(json.load(f))
    except (OSError, json.JSONDecodeError):
        return {"nodes": {}}


def _write(status_dir, hb):
    os.makedirs(status_dir, exist_ok=True)
    with open(_hb_path(status_dir), "w", encoding="utf-8") as f:
        json.dump(hb, f, indent=2)


def write_node(status_dir, node_name, fields):
    """Mergea fields en hb['nodes'][node_name]. Preserva otros nodos."""
    hb = read(status_dir)
    nodes = hb.setdefault("nodes", {})
    entry = nodes.setdefault(node_name, {})
    entry.update(fields)
    _write(status_dir, hb)


def update_telegram(status_dir, provider, iso):
    """Telegram es global (cualquier nodo puede mandar el reporte)."""
    hb = read(status_dir)
    hb["last_telegram_iso"] = iso
    hb["last_telegram_provider"] = provider
    _write(status_dir, hb)


def iter_nodes(status_dir):
    """Yield (node_name, entry) por cada nodo en el heartbeat."""
    hb = read(status_dir)
    for name, entry in hb.get("nodes", {}).items():
        yield name, entry

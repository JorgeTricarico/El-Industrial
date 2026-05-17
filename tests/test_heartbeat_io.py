"""Tests para heartbeat_io (M4, 2026-05-17)."""
import json
import os
import sys

SCRIPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPT_DIR)

import heartbeat_io


def test_read_missing_returns_empty(tmp_path):
    assert heartbeat_io.read(str(tmp_path)) == {"nodes": {}}


def test_read_normalizes_legacy(tmp_path):
    """Schema viejo (campos top-level) se convierte al schema nuevo."""
    hb = tmp_path / "heartbeat.json"
    hb.write_text(json.dumps({
        "last_run": "2026-05-17T10:00:00",
        "node": "raspberrypi",
        "status": "ok",
        "version": "abc",
        "last_telegram_iso": "2026-05-17T22:00:00",
        "last_telegram_provider": "gemini",
    }))
    out = heartbeat_io.read(str(tmp_path))
    assert "raspberrypi" in out["nodes"]
    assert out["nodes"]["raspberrypi"]["status"] == "ok"
    assert out["nodes"]["raspberrypi"]["version"] == "abc"
    # Telegram queda global
    assert out["last_telegram_iso"] == "2026-05-17T22:00:00"
    assert out["last_telegram_provider"] == "gemini"


def test_write_node_creates_and_merges(tmp_path):
    heartbeat_io.write_node(str(tmp_path), "pi", {"last_run": "t1", "status": "ok"})
    heartbeat_io.write_node(str(tmp_path), "mint", {"last_run": "t2"})
    out = heartbeat_io.read(str(tmp_path))
    assert "pi" in out["nodes"] and "mint" in out["nodes"]
    assert out["nodes"]["pi"]["last_run"] == "t1"
    assert out["nodes"]["mint"]["last_run"] == "t2"


def test_write_node_preserves_other_nodes(tmp_path):
    """Update de un nodo no debe pisar a los otros."""
    heartbeat_io.write_node(str(tmp_path), "pi", {"last_run": "t1", "status": "ok"})
    heartbeat_io.write_node(str(tmp_path), "mint", {"last_run": "t2", "status": "ok"})
    heartbeat_io.write_node(str(tmp_path), "pi", {"last_run": "t3"})
    out = heartbeat_io.read(str(tmp_path))
    assert out["nodes"]["pi"]["last_run"] == "t3"
    assert out["nodes"]["pi"]["status"] == "ok"  # preservado del primer write
    assert out["nodes"]["mint"]["last_run"] == "t2"


def test_update_telegram_global(tmp_path):
    heartbeat_io.write_node(str(tmp_path), "pi", {"last_run": "t1"})
    heartbeat_io.update_telegram(str(tmp_path), "gemini", "2026-05-17T22:00:00")
    out = heartbeat_io.read(str(tmp_path))
    assert out["last_telegram_iso"] == "2026-05-17T22:00:00"
    assert out["last_telegram_provider"] == "gemini"
    # Nodos siguen ahi
    assert out["nodes"]["pi"]["last_run"] == "t1"

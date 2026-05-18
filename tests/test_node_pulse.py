"""Tests para node_pulse (H1, 2026-05-18)."""
import os
import sys
import json

SCRIPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPT_DIR)

import node_pulse
import heartbeat_io


def test_pulse_writes_node_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(node_pulse, "STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(node_pulse.socket, "gethostname", lambda: "test-host")
    rc = node_pulse.main(["--outcome", "started"])
    assert rc == 0
    hb = heartbeat_io.read(str(tmp_path))
    assert "test-host" in hb["nodes"]
    entry = hb["nodes"]["test-host"]
    assert entry["last_outcome"] == "started"
    assert "last_run" in entry
    assert "version" in entry


def test_pulse_preserves_other_nodes(tmp_path, monkeypatch):
    """Si Pi ya pulso, mi pulso no lo borra."""
    heartbeat_io.write_node(str(tmp_path), "raspberrypi", {"last_run": "t1", "status": "ok"})
    monkeypatch.setattr(node_pulse, "STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(node_pulse.socket, "gethostname", lambda: "another-node")
    node_pulse.main(["--outcome", "dup_skip"])
    hb = heartbeat_io.read(str(tmp_path))
    assert "raspberrypi" in hb["nodes"]
    assert "another-node" in hb["nodes"]
    assert hb["nodes"]["raspberrypi"]["last_run"] == "t1"


def test_pulse_reads_role_from_registry(tmp_path, monkeypatch):
    """Si infra/nodes.yml declara el rol, lo registra en el pulso."""
    infra_dir = tmp_path / "infra"
    infra_dir.mkdir()
    (infra_dir / "nodes.yml").write_text(
        "nodes:\n"
        "  - hostname: my-test-node\n"
        "    role: backup\n"
        "    state: active\n"
    )
    monkeypatch.setattr(node_pulse, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(node_pulse, "STATUS_DIR", str(tmp_path / "status"))
    monkeypatch.setattr(node_pulse.socket, "gethostname", lambda: "my-test-node")
    node_pulse.main(["--outcome", "started"])
    hb = heartbeat_io.read(str(tmp_path / "status"))
    assert hb["nodes"]["my-test-node"]["role_declared"] == "backup"

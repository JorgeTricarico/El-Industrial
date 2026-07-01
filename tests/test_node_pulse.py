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


# --- effective_role: resolucion del rol OPERATIVO (fix 2026-07-01) ---

def test_effective_role_env_override_wins(monkeypatch):
    """EL_INDUSTRIAL_ROLE explicito gana sobre lo declarado en nodes.yml."""
    monkeypatch.setattr(node_pulse, "declared_role", lambda h: "primary")
    assert node_pulse.effective_role("cualquier-host", "backup") == "backup"
    assert node_pulse.effective_role("cualquier-host", "PRIMARY") == "primary"


def test_effective_role_backup_from_registry(monkeypatch):
    """Host declarado backup en nodes.yml resuelve backup (caso DESKTOP-MI43BOU)."""
    monkeypatch.setattr(node_pulse, "declared_role", lambda h: "backup")
    assert node_pulse.effective_role("DESKTOP-MI43BOU", None) == "backup"


def test_effective_role_primary_from_registry(monkeypatch):
    """Host declarado primary en nodes.yml resuelve primary (la Pi)."""
    monkeypatch.setattr(node_pulse, "declared_role", lambda h: "primary")
    assert node_pulse.effective_role("raspberrypi", None) == "primary"


def test_effective_role_cloud_and_dev_map_to_backup(monkeypatch):
    """Roles que no pushean precios reales (cloud_last_resort, dev) => backup."""
    monkeypatch.setattr(node_pulse, "declared_role", lambda h: "cloud_last_resort")
    assert node_pulse.effective_role("github-actions", None) == "backup"
    monkeypatch.setattr(node_pulse, "declared_role", lambda h: "dev")
    assert node_pulse.effective_role("mi-laptop", None) == "backup"


def test_effective_role_unknown_host_legacy_fallback(monkeypatch):
    """Host no registrado: fallback legacy por hostname (mint => backup)."""
    monkeypatch.setattr(node_pulse, "declared_role", lambda h: "unknown")
    assert node_pulse.effective_role("host-random", None) == "primary"
    assert node_pulse.effective_role("linux-mint-2", None) == "backup"


def test_resolve_role_cli_prints_role(monkeypatch, capsys):
    """--resolve-role imprime el rol y sale 0 sin escribir heartbeat."""
    monkeypatch.setattr(node_pulse.socket, "gethostname", lambda: "raspberrypi")
    monkeypatch.setattr(node_pulse, "declared_role", lambda h: "primary")
    monkeypatch.delenv("EL_INDUSTRIAL_ROLE", raising=False)
    rc = node_pulse.main(["--resolve-role"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "primary"

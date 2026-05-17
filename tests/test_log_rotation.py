"""Tests para log_rotation (M3, 2026-05-17)."""
import gzip
import os
import sys

SCRIPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPT_DIR)

import log_rotation


def test_no_rotation_under_threshold(tmp_path):
    src = tmp_path / "metrics.jsonl"
    src.write_text("a" * 100)  # ~100 bytes
    archive = tmp_path / "archive"
    info = log_rotation.rotate_file(str(src), str(archive), max_mb=10)
    assert info["rotated"] is False
    assert src.exists()
    assert not archive.exists()


def test_rotation_when_over_threshold(tmp_path):
    src = tmp_path / "metrics.jsonl"
    src.write_bytes(b"x" * (2 * 1024 * 1024))  # 2MB
    archive = tmp_path / "archive"
    info = log_rotation.rotate_file(str(src), str(archive), max_mb=1)
    assert info["rotated"] is True
    assert info["archive_path"].endswith(".gz")
    assert os.path.exists(info["archive_path"])
    # Original truncado a vacio
    assert src.read_bytes() == b""
    # Archive contiene el contenido original
    with gzip.open(info["archive_path"], "rb") as f:
        assert f.read() == b"x" * (2 * 1024 * 1024)


def test_rotation_appends_to_existing_month_archive(tmp_path):
    src = tmp_path / "cron_log.txt"
    archive = tmp_path / "archive"

    src.write_bytes(b"first_batch_" * 200000)  # >2MB
    info1 = log_rotation.rotate_file(str(src), str(archive), max_mb=1)
    assert info1["rotated"]

    src.write_bytes(b"second_batch_" * 200000)
    info2 = log_rotation.rotate_file(str(src), str(archive), max_mb=1)
    assert info2["rotated"]
    # Mismo archivo (mismo mes) — debe contener ambos
    assert info1["archive_path"] == info2["archive_path"]
    with gzip.open(info2["archive_path"], "rb") as f:
        content = f.read()
    assert b"first_batch_" in content
    assert b"second_batch_" in content


def test_rotate_all_returns_per_target_info(tmp_path, monkeypatch):
    metrics = tmp_path / "status" / "metrics.jsonl"
    cron_log = tmp_path / "reports" / "cron_log.txt"
    metrics.parent.mkdir(parents=True)
    cron_log.parent.mkdir(parents=True)
    metrics.write_bytes(b"a" * (2 * 1024 * 1024))
    cron_log.write_text("x")  # chiquito, no rota

    monkeypatch.setattr(log_rotation, "_default_targets", lambda: [
        (str(metrics), str(tmp_path / "status" / "archive")),
        (str(cron_log), str(tmp_path / "reports" / "archive")),
    ])
    res = log_rotation.rotate_all(max_mb=1)
    assert res[0]["rotated"] is True
    assert res[1]["rotated"] is False

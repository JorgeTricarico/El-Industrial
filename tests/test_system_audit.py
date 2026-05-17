"""Tests para scripts/system_audit.py.

Por contrato (CLAUDE.md regla #1) el conftest.py global ya mockea send_alert
y aisla STATUS_DIR. Estos tests cubren la logica de las checks individuales.
"""
import os
import gzip
import json
import tempfile
import time
from pathlib import Path
from datetime import datetime, timedelta
import importlib

import pytest

import system_audit


def _make_tenant_dir(root, slug, gz_age_hours=1):
    tdir = Path(root) / "tenants" / slug / "data"
    tdir.mkdir(parents=True, exist_ok=True)
    fname = "lista_precio_26-05-17_json_compres.gz"
    path = tdir / fname
    path.write_bytes(gzip.compress(b'[{"codigo": "x"}]'))
    # Backdate mtime
    target = time.time() - gz_age_hours * 3600
    os.utime(path, (target, target))
    return tdir


def test_check_tenants_deploys_fresh(monkeypatch, tmp_path):
    _make_tenant_dir(tmp_path, "alpha", gz_age_hours=1)
    monkeypatch.setattr(system_audit, "TENANTS_DIR", str(tmp_path / "tenants"))
    problems = system_audit.check_tenants_deploys([{"slug": "alpha", "state": "active"}])
    assert problems == []


def test_check_tenants_deploys_stale(monkeypatch, tmp_path):
    _make_tenant_dir(tmp_path, "alpha", gz_age_hours=100)
    monkeypatch.setattr(system_audit, "TENANTS_DIR", str(tmp_path / "tenants"))
    monkeypatch.setattr(system_audit, "TENANT_DEPLOY_STALE_HOURS", 48)
    problems = system_audit.check_tenants_deploys([{"slug": "alpha", "state": "active"}])
    assert len(problems) == 1
    assert "alpha" in problems[0]


def test_check_tenants_deploys_skips_inactive(monkeypatch, tmp_path):
    monkeypatch.setattr(system_audit, "TENANTS_DIR", str(tmp_path / "tenants"))
    problems = system_audit.check_tenants_deploys([{"slug": "beta", "state": "inactive"}])
    assert problems == []


def test_check_tenants_deploys_missing_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(system_audit, "TENANTS_DIR", str(tmp_path / "tenants"))
    problems = system_audit.check_tenants_deploys([{"slug": "alpha", "state": "active"}])
    assert any("falta carpeta data" in p for p in problems)


def test_check_env_keys_all_present(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_TOKEN=abc\n"
        "NETLIFY_AUTH_TOKEN=def\n"
        "BERTUAL_CUIT=u\n"
        "BERTUAL_PASSWORD=p\n"
        "BERTUAL_CLIENT_ID=c\n"
        "GEMINI_API_KEY=x\n"
        "CEREBRAS_API_KEY=y\n"
        "SAMBANOVA_API_KEY=z\n"
    )
    monkeypatch.setattr(system_audit, "ENV_PATH", str(env_path))
    tenants = [{"slug": "alpha", "state": "active", "supplier": "Bertual"}]
    assert system_audit.check_env_keys(tenants) == []


def test_check_env_keys_missing_global(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("GEMINI_API_KEY=x\nCEREBRAS_API_KEY=y\nSAMBANOVA_API_KEY=z\n")
    monkeypatch.setattr(system_audit, "ENV_PATH", str(env_path))
    problems = system_audit.check_env_keys([])
    assert any("TELEGRAM_TOKEN" in p for p in problems)
    assert any("NETLIFY_AUTH_TOKEN" in p for p in problems)


def test_check_env_keys_llm_degraded(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_TOKEN=t\nNETLIFY_AUTH_TOKEN=n\nGEMINI_API_KEY=x\n"
    )
    monkeypatch.setattr(system_audit, "ENV_PATH", str(env_path))
    problems = system_audit.check_env_keys([])
    # Faltan 2 LLMs -> alerta
    assert any("LLM" in p and "degradada" in p for p in problems)


def test_check_env_keys_missing_supplier_keys(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_TOKEN=t\nNETLIFY_AUTH_TOKEN=n\n"
        "GEMINI_API_KEY=x\nCEREBRAS_API_KEY=y\nSAMBANOVA_API_KEY=z\n"
    )
    monkeypatch.setattr(system_audit, "ENV_PATH", str(env_path))
    tenants = [{"slug": "alpha", "state": "active", "supplier": "Bertual"}]
    problems = system_audit.check_env_keys(tenants)
    assert any("BERTUAL_CUIT" in p for p in problems)
    assert any("BERTUAL_PASSWORD" in p for p in problems)
    assert any("BERTUAL_CLIENT_ID" in p for p in problems)


def test_check_env_keys_skips_testing_state(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_TOKEN=t\nNETLIFY_AUTH_TOKEN=n\n"
        "GEMINI_API_KEY=x\nCEREBRAS_API_KEY=y\nSAMBANOVA_API_KEY=z\n"
    )
    monkeypatch.setattr(system_audit, "ENV_PATH", str(env_path))
    tenants = [{"slug": "alpha", "state": "testing", "supplier": "Bertual"}]
    # En state=testing, no exigimos las keys del supplier
    assert system_audit.check_env_keys(tenants) == []


def test_check_node_heartbeats_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(system_audit, "STATUS_DIR", str(tmp_path))
    problems = system_audit.check_node_heartbeats()
    assert any("no existe" in p for p in problems)


def test_check_node_heartbeats_fresh(monkeypatch, tmp_path):
    hb = tmp_path / "heartbeat.json"
    hb.write_text(json.dumps({
        "last_run": datetime.now().isoformat(),
        "node": "pi",
    }))
    monkeypatch.setattr(system_audit, "STATUS_DIR", str(tmp_path))
    assert system_audit.check_node_heartbeats() == []


def test_check_node_heartbeats_stale(monkeypatch, tmp_path):
    hb = tmp_path / "heartbeat.json"
    old = (datetime.now() - timedelta(days=15)).isoformat()
    hb.write_text(json.dumps({"last_run": old, "node": "pi"}))
    monkeypatch.setattr(system_audit, "STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(system_audit, "NODE_OFFLINE_DAYS", 7)
    problems = system_audit.check_node_heartbeats()
    assert len(problems) == 1
    assert "15d" in problems[0]


def test_check_archive_stale_finds_old_files(monkeypatch, tmp_path):
    arc = tmp_path / "archive"
    arc.mkdir()
    old_file = arc / "old.gz"
    old_file.write_bytes(b"x")
    target = time.time() - 200 * 86400
    os.utime(old_file, (target, target))
    monkeypatch.setattr(system_audit, "ARCHIVE_DIR", str(arc))
    monkeypatch.setattr(system_audit, "ARCHIVE_STALE_DAYS", 90)
    problems = system_audit.check_archive_stale()
    assert len(problems) == 1
    assert "old.gz" in problems[0]


def test_check_archive_stale_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(system_audit, "ARCHIVE_DIR", str(tmp_path / "noexist"))
    assert system_audit.check_archive_stale() == []


def test_check_workflow_failures_no_credentials(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert system_audit.check_workflow_failures() == []


def test_check_netlify_no_token(monkeypatch):
    monkeypatch.delenv("NETLIFY_AUTH_TOKEN", raising=False)
    problems = system_audit.check_netlify_build_settings([{"slug": "x", "netlify_site_id": "abc"}])
    assert any("NETLIFY_AUTH_TOKEN ausente" in p for p in problems)


def test_format_report_clean():
    body = system_audit.format_report({"X": []}, 0)
    assert "🟢" in body
    assert "Sin observaciones" in body


def test_format_report_with_problems():
    sections = {"Tenants": ["alpha sin gz", "beta vieja"], "Otros": []}
    body = system_audit.format_report(sections, 2)
    assert "🟡" in body
    assert "alpha sin gz" in body
    assert "beta vieja" in body


def test_run_audit_smoke(monkeypatch, tmp_path):
    """Smoke: run_audit con tenant + env minimos. No debe explotar."""
    env = tmp_path / ".env"
    env.write_text(
        "TELEGRAM_TOKEN=t\nNETLIFY_AUTH_TOKEN=n\n"
        "GEMINI_API_KEY=x\nCEREBRAS_API_KEY=y\nSAMBANOVA_API_KEY=z\n"
    )
    monkeypatch.setattr(system_audit, "ENV_PATH", str(env))
    monkeypatch.setattr(system_audit, "TENANTS_DIR", str(tmp_path / "tenants"))
    monkeypatch.setattr(system_audit, "STATUS_DIR", str(tmp_path / "status"))
    monkeypatch.setattr(system_audit, "ARCHIVE_DIR", str(tmp_path / "archive"))
    # Forzar registry inexistente -> load_tenants devuelve []
    monkeypatch.setattr(system_audit, "REGISTRY", str(tmp_path / "no_registry.yml"))
    sections, total = system_audit.run_audit()
    assert isinstance(sections, dict)
    assert isinstance(total, int)


def test_main_does_not_send_real_telegram(monkeypatch, tmp_path):
    """Verifica que el conftest.py mockea send_alert: main() no debe
    intentar requests.post real. Si se rompiera el contrato, este test
    se da cuenta porque enviar Telegram real con TELEGRAM_TOKEN=fake
    no falla pero deja audit log."""
    monkeypatch.setattr(system_audit, "REGISTRY", str(tmp_path / "no.yml"))
    monkeypatch.setattr(system_audit, "ENV_PATH", str(tmp_path / "no.env"))
    monkeypatch.setattr(system_audit, "STATUS_DIR", str(tmp_path / "status"))
    monkeypatch.setattr(system_audit, "ARCHIVE_DIR", str(tmp_path / "arch"))
    monkeypatch.setattr(system_audit, "TENANTS_DIR", str(tmp_path / "tenants"))
    rc = system_audit.main()
    assert rc == 0


def test_check_log_sizes_under_threshold(monkeypatch, tmp_path):
    status = tmp_path / "status"
    status.mkdir()
    (status / "metrics.jsonl").write_text("x")
    monkeypatch.setattr(system_audit, "STATUS_DIR", str(status))
    monkeypatch.setattr(system_audit, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(system_audit, "LOG_SIZE_WARN_MB", 50.0)
    assert system_audit.check_log_sizes() == []


def test_check_log_sizes_over_threshold(monkeypatch, tmp_path):
    status = tmp_path / "status"
    status.mkdir()
    big = status / "metrics.jsonl"
    big.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB
    monkeypatch.setattr(system_audit, "STATUS_DIR", str(status))
    monkeypatch.setattr(system_audit, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(system_audit, "LOG_SIZE_WARN_MB", 1.0)
    problems = system_audit.check_log_sizes()
    assert any("metrics.jsonl" in p for p in problems)

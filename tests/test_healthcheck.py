"""Tests para healthcheck.py — detecta heartbeat viejo, API fallida, status no-ok."""
import os
import sys
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
import healthcheck  # noqa: E402


def write_heartbeat(tmp_path, last_run_iso, status="ok"):
    status_dir = tmp_path / "status"
    status_dir.mkdir(exist_ok=True)
    payload = {"last_run": last_run_iso, "node": "test-node", "status": status, "duration_s": 1.0}
    (status_dir / "heartbeat.json").write_text(json.dumps(payload))
    return status_dir


def write_metrics(tmp_path, entries):
    status_dir = tmp_path / "status"
    status_dir.mkdir(exist_ok=True)
    with open(status_dir / "metrics.jsonl", "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return status_dir


def test_diagnose_ok_con_heartbeat_reciente(tmp_path, monkeypatch):
    iso = datetime.now().isoformat()
    write_heartbeat(tmp_path, iso)
    monkeypatch.setattr(healthcheck, "STATUS_DIR", str(tmp_path / "status"))
    # Aislar de HTTP real al sitio publico (data del repo puede ser de ayer
    # cuando el test corre, y detect_public_site_stale lo marca stale).
    monkeypatch.setattr(healthcheck, "detect_public_site_stale", lambda: [])
    status, problems = healthcheck.diagnose()
    assert status == "ok"
    assert problems == []


def test_diagnose_alerta_si_heartbeat_viejo(tmp_path, monkeypatch):
    old_iso = (datetime.now() - timedelta(hours=30)).isoformat()
    write_heartbeat(tmp_path, old_iso)
    monkeypatch.setattr(healthcheck, "STATUS_DIR", str(tmp_path / "status"))
    status, problems = healthcheck.diagnose()
    assert status == "alert"
    assert any("Heartbeat viejo" in p for p in problems)


def test_diagnose_alerta_si_no_existe_heartbeat(tmp_path, monkeypatch):
    (tmp_path / "status").mkdir()
    monkeypatch.setattr(healthcheck, "STATUS_DIR", str(tmp_path / "status"))
    status, problems = healthcheck.diagnose()
    assert status == "alert"
    assert any("Sin heartbeat" in p for p in problems)


def test_diagnose_alerta_si_status_no_ok(tmp_path, monkeypatch):
    iso = datetime.now().isoformat()
    write_heartbeat(tmp_path, iso, status="api_fail")
    monkeypatch.setattr(healthcheck, "STATUS_DIR", str(tmp_path / "status"))
    status, problems = healthcheck.diagnose()
    assert status == "alert"
    assert any("api_fail" in p for p in problems)


def test_diagnose_alerta_si_3_ultimas_corridas_fallaron(tmp_path, monkeypatch):
    iso = datetime.now().isoformat()
    write_heartbeat(tmp_path, iso)
    # 3 corridas seguidas con api_fail
    write_metrics(tmp_path, [
        {"ts": iso, "api": "api_fail", "node": "test"},
        {"ts": iso, "api": "api_fail", "node": "test"},
        {"ts": iso, "api": "api_fail", "node": "test"},
    ])
    monkeypatch.setattr(healthcheck, "STATUS_DIR", str(tmp_path / "status"))
    status, problems = healthcheck.diagnose()
    assert status == "alert"
    assert any("fallaron contra la API Bertual" in p for p in problems)


def test_diagnose_ignora_eventos_sin_campo_api(tmp_path, monkeypatch):
    """metrics.jsonl puede tener eventos de nightly_report (sin campo 'api'); deben ignorarse."""
    iso = datetime.now().isoformat()
    write_heartbeat(tmp_path, iso)
    write_metrics(tmp_path, [
        {"ts": iso, "event": "llm_used", "detail": "gemini"},  # nightly_report log
        {"ts": iso, "api": "ok", "node": "test"},  # update_products log
    ])
    monkeypatch.setattr(healthcheck, "STATUS_DIR", str(tmp_path / "status"))
    monkeypatch.setattr(healthcheck, "detect_public_site_stale", lambda: [])
    status, _ = healthcheck.diagnose()
    assert status == "ok"


@pytest.mark.allow_real_send
@patch('healthcheck.requests.post')
@patch('healthcheck.TELEGRAM_TOKEN', 'fake')
@patch('healthcheck.TELEGRAM_CHAT_ID', '123')
def test_send_alert_llama_telegram(mock_post):
    m = MagicMock(); m.ok = True
    mock_post.return_value = m
    ok = healthcheck.send_alert(["Problema 1", "Problema 2"])
    assert ok is True
    assert mock_post.called
    payload = mock_post.call_args[1]["data"]
    assert "Problema 1" in payload["text"]
    assert "Problema 2" in payload["text"]


@patch('healthcheck.TELEGRAM_TOKEN', None)
@patch('healthcheck.TELEGRAM_CHAT_ID', None)
def test_send_alert_no_credenciales_devuelve_false():
    assert healthcheck.send_alert(["x"]) is False


# ============ DRIFT DE VERSION ============

@patch('healthcheck.subprocess.check_output')
@patch('healthcheck.subprocess.check_call')
def test_drift_detecta_version_distinta(mock_call, mock_out):
    """Si la version de algun nodo != origin/main, debe reportar drift."""
    mock_call.return_value = 0
    mock_out.return_value = b"deadbee\n"
    hb = {"nodes": {"raspberrypi": {"version": "cafe123"}}}
    drifts = healthcheck.detect_version_drift(hb)
    assert len(drifts) == 1
    assert "cafe123" in drifts[0] and "deadbee" in drifts[0]
    assert "raspberrypi" in drifts[0]


@patch('healthcheck.subprocess.check_output')
@patch('healthcheck.subprocess.check_call')
def test_drift_silencioso_si_versiones_coinciden(mock_call, mock_out):
    mock_call.return_value = 0
    mock_out.return_value = b"cafe123\n"
    hb = {"nodes": {"raspberrypi": {"version": "cafe123"}}}
    assert healthcheck.detect_version_drift(hb) == []


def test_drift_silencioso_si_heartbeat_sin_version():
    """Heartbeats sin version: no alertar."""
    assert healthcheck.detect_version_drift({"nodes": {}}) == []
    assert healthcheck.detect_version_drift(None) == []


@patch('healthcheck.subprocess.check_call', side_effect=FileNotFoundError("git no instalado"))
def test_drift_silencioso_si_git_falla(_mock):
    """Si git no responde (sin red, sin git), no alertamos por eso."""
    hb = {"nodes": {"x": {"version": "cafe123"}}}
    assert healthcheck.detect_version_drift(hb) == []


# ============ DETECCION DE SITIO PUBLICO CONGELADO ============

def _write_registry(tmp_path, content):
    d = tmp_path / "tenants"
    d.mkdir(exist_ok=True)
    (d / "_registry.yml").write_text(content)


@patch('healthcheck.requests.get')
def test_sitio_publico_actualizado_no_alerta(mock_get, tmp_path, monkeypatch):
    """Si el pointer publico apunta al .gz de hoy, no debe alertar."""
    from datetime import datetime
    today = datetime.now().strftime("%y-%m-%d")
    mock_resp = MagicMock(ok=True, text=f"data/lista_precio_{today}_json_compres.gz")
    mock_get.return_value = mock_resp
    monkeypatch.setattr(healthcheck, "BASE_DIR", str(tmp_path))
    _write_registry(tmp_path, """
tenants:
  - slug: el-industrial
    state: active
    netlify_url: "https://el-industrial.netlify.app"
""")
    problems = healthcheck.detect_public_site_stale()
    assert problems == []


@patch('healthcheck.requests.get')
def test_sitio_publico_congelado_alerta(mock_get, tmp_path, monkeypatch):
    """Si el pointer publico apunta a un .gz de hace mucho, debe alertar."""
    mock_resp = MagicMock(ok=True, text="data/lista_precio_26-04-26_json_compres.gz")
    mock_get.return_value = mock_resp
    monkeypatch.setattr(healthcheck, "BASE_DIR", str(tmp_path))
    _write_registry(tmp_path, """
tenants:
  - slug: el-industrial
    state: active
    netlify_url: "https://el-industrial.netlify.app"
""")
    problems = healthcheck.detect_public_site_stale()
    assert any("el-industrial" in p and "deploy a Netlify NO esta llegando" in p for p in problems), problems


@patch('healthcheck.requests.get', side_effect=Exception("network down"))
def test_sitio_publico_red_caida_se_reporta(_mock, tmp_path, monkeypatch):
    monkeypatch.setattr(healthcheck, "BASE_DIR", str(tmp_path))
    _write_registry(tmp_path, """
tenants:
  - slug: el-industrial
    state: active
    netlify_url: "https://el-industrial.netlify.app"
""")
    problems = healthcheck.detect_public_site_stale()
    # Cualquier mensaje que mencione "el-industrial" cuenta como alerta
    assert any("el-industrial" in p for p in problems)


def test_sitio_publico_skip_tenants_inactive(tmp_path, monkeypatch):
    """Tenants en estado inactive no se chequean."""
    monkeypatch.setattr(healthcheck, "BASE_DIR", str(tmp_path))
    _write_registry(tmp_path, """
tenants:
  - slug: pausado
    state: inactive
    netlify_url: "https://pausado.netlify.app"
""")
    problems = healthcheck.detect_public_site_stale()
    assert problems == []

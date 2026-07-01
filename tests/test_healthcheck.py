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


def _write_multinode(tmp_path, nodes):
    status_dir = tmp_path / "status"
    status_dir.mkdir(exist_ok=True)
    (status_dir / "heartbeat.json").write_text(json.dumps({"nodes": nodes}))
    return status_dir


def test_no_alerta_por_nodo_con_fallo_viejo(tmp_path, monkeypatch):
    """El falso positivo que reporto Jorge: un backup con supplier_down VIEJO
    (no volvio a correr) NO debe alertar si otro nodo esta fresco y ok."""
    old = (datetime.now() - timedelta(hours=35)).isoformat()
    recent = datetime.now().isoformat()
    _write_multinode(tmp_path, {
        "DESKTOP-MI43BOU": {"last_run": old, "last_outcome": "supplier_down", "status": "supplier_down"},
        "raspberrypi": {"last_run": recent, "last_outcome": "updated", "status": "ok"},
    })
    monkeypatch.setattr(healthcheck, "STATUS_DIR", str(tmp_path / "status"))
    monkeypatch.setattr(healthcheck, "detect_public_site_stale", lambda: [])
    status, problems = healthcheck.diagnose()
    assert status == "ok", f"un fallo viejo de un nodo que no volvio a correr no debe alertar: {problems}"


def test_no_alerta_por_backup_que_dup_skipea(tmp_path, monkeypatch):
    """Backup con last_run RECIENTE y last_outcome=dup_skip (sano) pero 'status'
    VIEJO (supplier_down): no debe alertar. status queda viejo porque dup_skip
    no corre update_products; usamos last_outcome que es el signal fresco."""
    recent = datetime.now().isoformat()
    _write_multinode(tmp_path, {
        "DESKTOP-MI43BOU": {"last_run": recent, "last_outcome": "dup_skip", "status": "supplier_down"},
        "raspberrypi": {"last_run": recent, "last_outcome": "updated", "status": "ok"},
    })
    monkeypatch.setattr(healthcheck, "STATUS_DIR", str(tmp_path / "status"))
    monkeypatch.setattr(healthcheck, "detect_public_site_stale", lambda: [])
    status, problems = healthcheck.diagnose()
    assert status == "ok", f"un backup que dup_skipea no debe alertar: {problems}"


def test_alerta_por_fallo_RECIENTE_de_nodo(tmp_path, monkeypatch):
    """Si un nodo fallo RECIEN (last_outcome de fallo + reciente), SI alerta.
    No perdemos deteccion de fallos reales al filtrar los viejos."""
    recent = datetime.now().isoformat()
    _write_multinode(tmp_path, {
        "raspberrypi": {"last_run": recent, "last_outcome": "supplier_down", "status": "supplier_down"},
    })
    monkeypatch.setattr(healthcheck, "STATUS_DIR", str(tmp_path / "status"))
    monkeypatch.setattr(healthcheck, "detect_public_site_stale", lambda: [])
    status, problems = healthcheck.diagnose()
    assert status == "alert"
    assert any("ultima corrida fallo" in p for p in problems)


def test_diagnose_alerta_si_supplier_down_sostenido(tmp_path, monkeypatch):
    """P13: 3 supplier_down seguidos = outage sostenido, debe alertar.

    Antes solo se miraba api_fail, dejando pasar supplier_down sostenido hasta
    el stale check de 26h. Gap cerrado 2026-07-01."""
    iso = datetime.now().isoformat()
    write_heartbeat(tmp_path, iso)
    write_metrics(tmp_path, [
        {"ts": iso, "api": "supplier_down", "node": "raspberrypi", "tenant": "el-industrial"},
        {"ts": iso, "api": "supplier_down", "node": "raspberrypi", "tenant": "el-industrial"},
        {"ts": iso, "api": "supplier_down", "node": "raspberrypi", "tenant": "el-industrial"},
    ])
    monkeypatch.setattr(healthcheck, "STATUS_DIR", str(tmp_path / "status"))
    monkeypatch.setattr(healthcheck, "detect_public_site_stale", lambda: [])
    status, problems = healthcheck.diagnose()
    assert status == "alert"
    assert any("Proveedor caido sostenido" in p for p in problems)


def test_diagnose_no_alerta_supplier_down_aislado(tmp_path, monkeypatch):
    """P13: un supplier_down aislado NO debe escalar (lo cubre el filler)."""
    iso = datetime.now().isoformat()
    write_heartbeat(tmp_path, iso)
    write_metrics(tmp_path, [
        {"ts": iso, "api": "supplier_down", "node": "raspberrypi", "tenant": "el-industrial"},
    ])
    monkeypatch.setattr(healthcheck, "STATUS_DIR", str(tmp_path / "status"))
    monkeypatch.setattr(healthcheck, "detect_public_site_stale", lambda: [])
    status, problems = healthcheck.diagnose()
    assert status == "ok", f"un solo supplier_down no debe alertar: {problems}"


def test_diagnose_no_alerta_si_proveedor_se_recupero(tmp_path, monkeypatch):
    """P13: si la corrida mas reciente fue 'ok', el streak se corta (no alertar)."""
    iso = datetime.now().isoformat()
    write_heartbeat(tmp_path, iso)
    # Orden de archivo: mas viejo -> mas nuevo. last_n_runs lee en reversa,
    # asi que la ultima ('ok') rompe el streak.
    write_metrics(tmp_path, [
        {"ts": iso, "api": "supplier_down", "node": "raspberrypi", "tenant": "el-industrial"},
        {"ts": iso, "api": "supplier_down", "node": "raspberrypi", "tenant": "el-industrial"},
        {"ts": iso, "api": "ok", "node": "raspberrypi", "tenant": "el-industrial"},
    ])
    monkeypatch.setattr(healthcheck, "STATUS_DIR", str(tmp_path / "status"))
    monkeypatch.setattr(healthcheck, "detect_public_site_stale", lambda: [])
    status, problems = healthcheck.diagnose()
    assert status == "ok", f"proveedor recuperado no debe alertar: {problems}"


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

def _mock_git_outputs(short_sha, commit_iso):
    """Helper: mockea subprocess.check_output para devolver el SHA en la 1er
    llamada y el ISO del commit en la 2da."""
    return [
        short_sha.encode() + b"\n",
        commit_iso.encode() + b"\n",
    ]


@patch('healthcheck.subprocess.check_output')
@patch('healthcheck.subprocess.check_call')
def test_drift_detecta_version_distinta(mock_call, mock_out):
    """Si la version de un nodo != origin/main Y el nodo pulleo despues del
    commit, debe reportar drift (git pull esta roto)."""
    mock_call.return_value = 0
    # commit en origin del 2026-05-18 10:00, nodo pulleo a las 11:00 (post-commit)
    mock_out.side_effect = _mock_git_outputs("deadbee", "2026-05-18T10:00:00")
    hb = {"nodes": {"raspberrypi": {
        "version": "cafe123",
        "last_pulled_iso": "2026-05-18T11:00:00",
    }}}
    drifts = healthcheck.detect_version_drift(hb)
    assert len(drifts) == 1
    assert "cafe123" in drifts[0] and "deadbee" in drifts[0]
    assert "raspberrypi" in drifts[0]


@patch('healthcheck.subprocess.check_output')
@patch('healthcheck.subprocess.check_call')
def test_drift_silencioso_si_nodo_no_pulleo_post_commit(mock_call, mock_out):
    """Si el nodo pulleo ANTES del commit nuevo, NO es drift (es 'todavia no
    le toco el cron'). Caso lunes-mañana antes del cron de las 20:00."""
    mock_call.return_value = 0
    # commit en origin 2026-05-18 10:00, nodo pulleo el 2026-05-17 22:00 (Sabado, pre-commit)
    mock_out.side_effect = _mock_git_outputs("deadbee", "2026-05-18T10:00:00")
    hb = {"nodes": {"raspberrypi": {
        "version": "cafe123",
        "last_pulled_iso": "2026-05-17T22:00:00",
    }}}
    assert healthcheck.detect_version_drift(hb) == []


@patch('healthcheck.subprocess.check_output')
@patch('healthcheck.subprocess.check_call')
def test_drift_silencioso_si_versiones_coinciden(mock_call, mock_out):
    mock_call.return_value = 0
    mock_out.side_effect = _mock_git_outputs("cafe123", "2026-05-18T10:00:00")
    hb = {"nodes": {"raspberrypi": {
        "version": "cafe123",
        "last_pulled_iso": "2026-05-18T11:00:00",
    }}}
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


@patch('healthcheck.requests.get')
def test_sitio_publico_skip_tenants_testing(mock_get, tmp_path, monkeypatch):
    """Tenants en estado testing no se chequean: su data es estatica por diseno."""
    monkeypatch.setattr(healthcheck, "BASE_DIR", str(tmp_path))
    _write_registry(tmp_path, """
tenants:
  - slug: demo-electricidad
    state: testing
    netlify_url: "https://demo-electricidad.netlify.app"
""")
    problems = healthcheck.detect_public_site_stale()
    assert problems == []
    mock_get.assert_not_called()


@patch('healthcheck.requests.get')
def test_sitio_publico_ayer_no_genera_falso_positivo(mock_get, tmp_path, monkeypatch):
    """Archivo de ayer no debe alertar si tiene < 26h desde el ultimo deploy posible.

    El cron corre hasta las 22:00 AR. Un archivo de ayer tiene como maximo
    (now - ayer_22:00) horas reales de stale. Sin el +20h este test fallaria
    cuando el runner de GH Actions arranca despues de las ~02:00 AR.
    """
    from datetime import datetime, timedelta
    # Fijamos 'now' a las 03:00 AR del dia siguiente al archivo → simula el
    # runner de GH Actions llegando tarde. Sin +20h seria 27h stale (falso
    # positivo); con +20h es 7h stale → OK.
    fake_now = datetime(2026, 5, 23, 3, 0, 0)
    monkeypatch.setattr(healthcheck, "datetime", type("_DT", (), {"now": staticmethod(lambda: fake_now), "strptime": datetime.strptime})())
    mock_resp = MagicMock(ok=True, text="data/lista_precio_26-05-22_json_compres.gz")
    mock_get.return_value = mock_resp
    monkeypatch.setattr(healthcheck, "BASE_DIR", str(tmp_path))
    _write_registry(tmp_path, """
tenants:
  - slug: el-industrial
    state: active
    netlify_url: "https://el-industrial.netlify.app"
""")
    problems = healthcheck.detect_public_site_stale()
    assert problems == [], f"Falso positivo con dato de ayer: {problems}"


@patch('healthcheck.requests.get')
def test_sitio_publico_anteayer_si_alerta(mock_get, tmp_path, monkeypatch):
    """Archivo de anteayer (genuinamente stale, > 26h desde ultimo deploy) si alerta."""
    from datetime import datetime
    # 03:00 AR del 23/05, archivo del 21/05. effective_deploy=21/05 20:00.
    # age_h = (23/05 03:00) - (21/05 20:00) = 31h > 26h → debe alertar.
    fake_now = datetime(2026, 5, 23, 3, 0, 0)
    monkeypatch.setattr(healthcheck, "datetime", type("_DT", (), {"now": staticmethod(lambda: fake_now), "strptime": datetime.strptime})())
    mock_resp = MagicMock(ok=True, text="data/lista_precio_26-05-21_json_compres.gz")
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


# ============ STALE TOLERANCE LUN-SAB ============

def test_expected_stale_hours_lunes_temprano_tolera_weekend(monkeypatch):
    """Lunes 8 AM: tolera 50h porque la data puede ser del Sabado."""
    import datetime as _dt
    fixed = _dt.datetime(2026, 5, 18, 8, 0)  # Lunes 8AM
    assert healthcheck._expected_stale_hours(fixed) == 50


def test_expected_stale_hours_domingo_tolera_weekend():
    import datetime as _dt
    fixed = _dt.datetime(2026, 5, 17, 12, 0)  # Domingo mediodia
    assert healthcheck._expected_stale_hours(fixed) == 50


def test_expected_stale_hours_lunes_tarde_threshold_normal():
    """Lunes 21:00: ya paso el cron 20:00, threshold normal."""
    import datetime as _dt
    fixed = _dt.datetime(2026, 5, 18, 21, 0)
    assert healthcheck._expected_stale_hours(fixed) == healthcheck.THRESHOLD_HOURS


def test_expected_stale_hours_martes_threshold_normal():
    """Martes cualquier hora: threshold 26h."""
    import datetime as _dt
    assert healthcheck._expected_stale_hours(_dt.datetime(2026, 5, 19, 9, 0)) == healthcheck.THRESHOLD_HOURS

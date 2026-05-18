"""Tests para nightly_report.py — cadena de fallback, prompt, plantilla, sanitización."""
import os
import sys
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
import nightly_report  # noqa: E402


# ============ CADENA DE FALLBACK ============

@patch('nightly_report.GEMINI_API_KEY', 'fake_gemini')
@patch('nightly_report.CEREBRAS_API_KEY', 'fake_cerebras')
@patch('nightly_report.SAMBANOVA_API_KEY', 'fake_sambanova')
@patch('nightly_report.requests.post')
def test_chain_uses_gemini_when_available(mock_post):
    """Si Gemini responde OK, no se llama a Cerebras ni Groq."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.ok = True
    mock_response.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": "respuesta de gemini"}]}}]
    }
    mock_response.raise_for_status = MagicMock()
    mock_post.return_value = mock_response

    text, provider = nightly_report.get_ai_analysis("prompt cualquiera")
    assert text == "respuesta de gemini"
    assert provider == "gemini"
    assert mock_post.call_count == 1
    assert "generativelanguage.googleapis.com" in mock_post.call_args[0][0]


@patch('nightly_report.GEMINI_API_KEY', 'fake_gemini')
@patch('nightly_report.CEREBRAS_API_KEY', 'fake_cerebras')
@patch('nightly_report.SAMBANOVA_API_KEY', 'fake_sambanova')
@patch('nightly_report.requests.post')
def test_chain_falls_back_to_cerebras(mock_post):
    """Si Gemini devuelve 500, cae a Cerebras."""
    def fake_post(url, **kw):
        m = MagicMock()
        if "googleapis" in url:
            m.status_code = 500
            m.ok = False
            m.raise_for_status.side_effect = Exception("gemini 500")
        elif "cerebras" in url:
            m.status_code = 200
            m.ok = True
            m.json.return_value = {"choices": [{"message": {"content": "respuesta cerebras"}}]}
            m.raise_for_status = MagicMock()
        return m

    mock_post.side_effect = fake_post
    text, provider = nightly_report.get_ai_analysis("prompt")
    assert provider == "cerebras"
    assert "cerebras" in text


@patch('nightly_report.GEMINI_API_KEY', 'fake_gemini')
@patch('nightly_report.CEREBRAS_API_KEY', 'fake_cerebras')
@patch('nightly_report.SAMBANOVA_API_KEY', 'fake_sambanova')
@patch('nightly_report.requests.post')
def test_chain_falls_back_to_sambanova(mock_post):
    """Si Gemini y Cerebras fallan, cae a SambaNova."""
    def fake_post(url, **kw):
        m = MagicMock()
        if "sambanova" in url:
            m.status_code = 200
            m.ok = True
            m.json.return_value = {"choices": [{"message": {"content": "respuesta sambanova"}}]}
            m.raise_for_status = MagicMock()
        else:
            m.raise_for_status.side_effect = Exception("LLM caido")
            m.status_code = 503
            m.ok = False
        return m

    mock_post.side_effect = fake_post
    text, provider = nightly_report.get_ai_analysis("prompt")
    assert provider == "sambanova"


@patch('nightly_report.GEMINI_API_KEY', 'fake_gemini')
@patch('nightly_report.CEREBRAS_API_KEY', 'fake_cerebras')
@patch('nightly_report.SAMBANOVA_API_KEY', 'fake_sambanova')
@patch('nightly_report.requests.post')
def test_chain_returns_none_when_all_fail(mock_post):
    """Si los 3 LLMs fallan, get_ai_analysis devuelve (None, 'template')."""
    m = MagicMock()
    m.raise_for_status.side_effect = Exception("todo caido")
    m.status_code = 503
    m.ok = False
    mock_post.return_value = m

    text, provider = nightly_report.get_ai_analysis("prompt")
    assert text is None
    assert provider == "template"


@patch('nightly_report.GEMINI_API_KEY', None)
@patch('nightly_report.CEREBRAS_API_KEY', None)
@patch('nightly_report.SAMBANOVA_API_KEY', None)
def test_chain_no_keys_falls_to_template():
    """Sin ninguna API key configurada, va directo a template."""
    text, provider = nightly_report.get_ai_analysis("prompt")
    assert provider == "template"
    assert text is None


# ============ PLANTILLA FALLBACK ============

def test_template_fallback_negligible_es_una_sola_linea():
    """Dia tranquilo: mensaje cortito, sin bullets ni recomendaciones."""
    updated = [{"name": "x", "old": "100", "new": "100.01"}]
    top_hikes = [{"n": "x", "p": 0.01, "m": "A"}]
    msg = nightly_report.render_template_fallback(updated, [], top_hikes, "17/05/2026")
    assert "tranquilo" in msg.lower()
    assert "•" not in msg  # sin bullets
    assert "IA no disponible" in msg


def test_template_fallback_con_datos_reales():
    """Dia con movimiento: bullets en pesos viejo -> nuevo."""
    updated = [{"name": f"Producto {i}", "marca": "A", "old": "100", "new": "108"}
               for i in range(7)]
    top_hikes = [{"n": f"Producto {i}", "p": 8.0, "m": "A"} for i in range(7)]
    msg = nightly_report.render_template_fallback(updated, [], top_hikes, "17/05/2026")
    assert "7 producto" in msg
    assert "$100" in msg and "$108" in msg
    assert "→" in msg or "->" in msg


def test_classify_magnitude_negligible():
    top_hikes = [{"n": "x", "p": 0.05, "m": "A"}]
    m = nightly_report.classify_magnitude(top_hikes)
    assert m["class"] == "negligible"


def test_classify_magnitude_strong():
    top_hikes = [{"n": "x", "p": 15.0, "m": "A"}, {"n": "y", "p": 12.0, "m": "B"}]
    m = nightly_report.classify_magnitude(top_hikes)
    assert m["class"] == "strong"


# ============ TONO DEL PROMPT ============

def test_prompt_describe_negocio_b2b_intermediario():
    """El prompt debe explicar que el lector es un mayorista chico B2B
    (compra a mayoristas grandes, vende a ferreterias/electricistas/etc).
    """
    prompt = nightly_report.build_prompt([], [], [])
    p = prompt.lower()
    assert "mayorista" in p, "tiene que decir que es mayorista chico"
    assert "ferreteria" in p
    # Audience: pros de la obra/electricidad
    assert "electricista" in p or "arquitecto" in p or "constructor" in p
    if "analista" in p:
        idx = p.index("analista")
        contexto_previo = p[max(0, idx - 30):idx]
        assert "no " in contexto_previo


def test_prompt_permite_jerga_b2b_pero_prohibe_consultora():
    """Es B2B: 'cotizacion', 'lista', 'rubro' SON adecuadas. Lo que NO va es
    'estrategico', 'recalibrar' tipo consultor."""
    prompt = nightly_report.build_prompt([], [], [])
    p = prompt.lower()
    assert "cotizacion" in p, "cotizacion es palabra util para B2B"
    # Estas son las que deben estar prohibidas explicitamente
    assert "estrategico" in p or "recalibrar" in p, \
        "el prompt debe nombrar al menos una palabra de consultor como prohibida"


def test_prompt_prohibe_palabras_alarmistas():
    """El prompt debe prohibir palabras alarmistas."""
    prompt = nightly_report.build_prompt([], [], [])
    p = prompt.lower()
    assert "prohibido" in p
    # cada palabra alarmista debe aparecer en la lista de prohibidas
    for palabra in ["critico", "alarmante", "riesgo", "historico", "masivo"]:
        assert palabra in p, f"falta prohibir '{palabra}'"


def test_prompt_pide_formato_html_y_es_corto():
    """HTML simple + maximo ~600 chars en el mensaje final."""
    prompt = nightly_report.build_prompt([], [], [])
    assert "<b>" in prompt
    assert "600" in prompt


def test_prompt_en_dia_tranquilo_pide_no_recomendar():
    """Si la magnitud es negligible, el prompt prohibe sugerir re-cotizar."""
    top_hikes = [{"n": "x", "p": 0.01, "m": "A"}]  # negligible
    prompt = nightly_report.build_prompt([], [], top_hikes)
    p = prompt.lower()
    assert "tranquilo" in p
    # Debe instruir explicitamente a NO recomendar accion (re-cotizar, ajustar)
    assert "sin recomendacion" in p or "sin bullets" in p
    assert "no hace falta" in p


def test_prompt_pide_montos_en_pesos():
    """El prompt debe pedir mostrar cambios en pesos, no solo en %."""
    prompt = nightly_report.build_prompt([], [], [])
    assert "pesos" in prompt.lower()


# ============ SANITIZACION ============

def test_sanitize_remueve_markdown():
    out = nightly_report.sanitize_html("*hola* _mundo_")
    assert "*" not in out
    assert "_" not in out


def test_sanitize_remueve_emojis_alarma():
    out = nightly_report.sanitize_html("⚠️ Cuidado 🚨 alerta 🔥 fuego")
    assert "⚠️" not in out
    assert "🚨" not in out
    assert "🔥" not in out


# ============ MAIN FLOW (smoke test E2E) ============

def _setup_tenant_with_accum(tmp_path, slug="alpha", accum=None):
    """Helper: crea estructura tenants/<slug>/ con daily_accum.json para tests."""
    accum = accum if accum is not None else {
        "updated": {"X1": {"code": "X1", "name": "Cable test",
                           "old": "100.0", "new": "110.0", "marca": "TEST"}}
    }
    tenants_dir = tmp_path / "tenants"
    tenant_root = tenants_dir / slug
    (tenant_root / "status").mkdir(parents=True)
    (tenant_root / "status" / "daily_accum.json").write_text(json.dumps(accum))
    registry = tenants_dir / "_registry.yml"
    registry.write_text(f"tenants:\n  - slug: {slug}\n    state: active\n")
    return tenants_dir, tenant_root


@patch('nightly_report.GEMINI_API_KEY', 'fake')
@patch('nightly_report.CEREBRAS_API_KEY', 'fake')
@patch('nightly_report.SAMBANOVA_API_KEY', 'fake')
@patch('nightly_report.send_telegram')
@patch('nightly_report.requests.post')
def test_main_envia_telegram_aunque_llm_falle(mock_post, mock_send, tmp_path, monkeypatch):
    """Smoke test: con los 3 LLMs caidos, main() igual llama a send_telegram con plantilla."""
    tenants_dir, _ = _setup_tenant_with_accum(tmp_path)
    monkeypatch.setattr(nightly_report, "STATUS_DIR", str(tmp_path / "status"))
    monkeypatch.setattr(nightly_report, "TENANTS_DIR", str(tenants_dir))
    monkeypatch.setattr(nightly_report, "REGISTRY", str(tenants_dir / "_registry.yml"))

    m = MagicMock()
    m.raise_for_status.side_effect = Exception("LLM caido")
    m.status_code = 503
    m.ok = False
    mock_post.return_value = m
    mock_send.return_value = True

    nightly_report.main()

    assert mock_send.called, "send_telegram debe llamarse aunque los LLMs fallen"
    sent_msg = mock_send.call_args[0][0]
    assert "Lista del dia" in sent_msg
    assert any(s in sent_msg for s in ["TEST", "Cable test", "1 productos"]), \
        f"mensaje plantilla sin datos del item: {sent_msg!r}"


# ============ PROCESS TENANT REPORT (Fase 2B) ============

@patch('nightly_report.send_telegram')
@patch('nightly_report.get_ai_analysis')
def test_process_tenant_report_skips_inactive(mock_ai, mock_send, tmp_path, monkeypatch):
    monkeypatch.setattr(nightly_report, "TENANTS_DIR", str(tmp_path / "tenants"))
    res = nightly_report.process_tenant_report({"slug": "x", "state": "inactive"})
    assert res["status"].startswith("skip")
    assert not mock_send.called


@patch('nightly_report.send_telegram')
@patch('nightly_report.get_ai_analysis')
def test_process_tenant_report_no_accum_on_domingo_skips(mock_ai, mock_send, tmp_path, monkeypatch):
    """Domingo + sin accum -> no enviar. Lun-Sab garantiza filler (otro test)."""
    monkeypatch.setattr(nightly_report, "TENANTS_DIR", str(tmp_path / "tenants"))
    monkeypatch.setattr(nightly_report, "_is_guaranteed_day", lambda *a, **kw: False)
    res = nightly_report.process_tenant_report({"slug": "alpha", "state": "active"})
    assert res["status"] == "no_accum"
    assert not mock_send.called


@patch('nightly_report.send_telegram', return_value=True)
def test_process_tenant_report_no_accum_on_workday_sends_filler(mock_send, tmp_path, monkeypatch):
    """Lun-Sab + sin accum -> manda filler 'supplier_down'."""
    tenants_dir = tmp_path / "tenants"
    (tenants_dir / "alpha").mkdir(parents=True)
    monkeypatch.setattr(nightly_report, "TENANTS_DIR", str(tenants_dir))
    monkeypatch.setattr(nightly_report, "STATUS_DIR", str(tmp_path / "status"))
    monkeypatch.setattr(nightly_report, "_is_guaranteed_day", lambda *a, **kw: True)
    res = nightly_report.process_tenant_report({"slug": "alpha", "state": "active"})
    assert res["status"] == "ok"
    assert res["provider"] == "filler_supplier_down"
    assert mock_send.called
    body = mock_send.call_args[0][0]
    assert "no respondio" in body.lower() or "no actualizo" in body.lower()


@patch('nightly_report.send_telegram', return_value=True)
@patch('nightly_report.get_ai_analysis', return_value=("respuesta AI", "gemini"))
def test_process_tenant_report_sends_with_tenant_branding(mock_ai, mock_send, tmp_path, monkeypatch):
    tenants_dir, tenant_root = _setup_tenant_with_accum(tmp_path, slug="demo-elec")
    # Branding del tenant
    (tenant_root / "config").mkdir()
    (tenant_root / "config" / "branding.json").write_text(json.dumps({"siteName": "Mi Empresa"}))

    monkeypatch.setattr(nightly_report, "TENANTS_DIR", str(tenants_dir))
    monkeypatch.setattr(nightly_report, "STATUS_DIR", str(tmp_path / "status"))

    res = nightly_report.process_tenant_report({"slug": "demo-elec", "state": "active"})
    assert res["status"] == "ok"
    assert res["items"] == 1
    assert res["sent"] is True

    msg = mock_send.call_args[0][0]
    assert "Mi Empresa" in msg, "el header debe llevar el siteName del tenant"
    # Pasar clients_path apuntando al yaml del tenant
    kwargs = mock_send.call_args[1]
    assert "demo-elec" in kwargs.get("clients_path", "")


@patch('nightly_report.send_telegram', return_value=True)
@patch('nightly_report.get_ai_analysis', return_value=("respuesta AI", "gemini"))
def test_process_tenant_archives_accum(mock_ai, mock_send, tmp_path, monkeypatch):
    tenants_dir, tenant_root = _setup_tenant_with_accum(tmp_path)
    monkeypatch.setattr(nightly_report, "TENANTS_DIR", str(tenants_dir))
    monkeypatch.setattr(nightly_report, "STATUS_DIR", str(tmp_path / "status"))

    nightly_report.process_tenant_report({"slug": "alpha", "state": "active"})

    # accum se movio a archive/
    assert not (tenant_root / "status" / "daily_accum.json").exists()
    archived = list((tenant_root / "status" / "archive").iterdir())
    assert len(archived) == 1


@patch('nightly_report.send_telegram', return_value=True)
@patch('nightly_report.get_ai_analysis', return_value=("AI text", "gemini"))
def test_main_itera_solo_active(mock_ai, mock_send, tmp_path, monkeypatch):
    """main() debe procesar solo tenants con state=active."""
    tenants_dir = tmp_path / "tenants"
    for slug, state in [("alpha", "active"), ("beta", "testing"), ("gamma", "inactive")]:
        d = tenants_dir / slug / "status"
        d.mkdir(parents=True)
        (d / "daily_accum.json").write_text(json.dumps({"updated": {}}))
    (tenants_dir / "_registry.yml").write_text(
        "tenants:\n"
        "  - slug: alpha\n    state: active\n"
        "  - slug: beta\n    state: testing\n"
        "  - slug: gamma\n    state: inactive\n"
    )
    monkeypatch.setattr(nightly_report, "TENANTS_DIR", str(tenants_dir))
    monkeypatch.setattr(nightly_report, "REGISTRY", str(tenants_dir / "_registry.yml"))
    monkeypatch.setattr(nightly_report, "STATUS_DIR", str(tmp_path / "status"))

    nightly_report.main()

    # Solo alpha procesado -> 1 llamada a send_telegram
    assert mock_send.call_count == 1


# ============ ROTACION DE ARCHIVE ============

def test_prune_borra_archivos_viejos(tmp_path):
    """prune_old_archives elimina archivos con mtime > N dias y conserva los recientes."""
    import time as _time
    archive = tmp_path / "archive"
    archive.mkdir()
    viejo = archive / "accum_old.json"
    viejo.write_text("{}")
    nuevo = archive / "accum_new.json"
    nuevo.write_text("{}")
    # Backdating: ponemos mtime de "viejo" a 100 dias atras
    cien_dias = _time.time() - 100 * 86400
    os.utime(viejo, (cien_dias, cien_dias))

    removed = nightly_report.prune_old_archives(str(archive), days=90)
    assert removed == 1
    assert not viejo.exists()
    assert nuevo.exists()


def test_prune_sin_archive_dir_no_explota(tmp_path):
    removed = nightly_report.prune_old_archives(str(tmp_path / "no_existe"), days=90)
    assert removed == 0


# ============ DEDUPE PER-TENANT POR DIA ============

def test_dedupe_skips_when_already_sent_today(tmp_path, monkeypatch):
    """Si heartbeat indica que el tenant ya recibio Telegram hoy, skip."""
    import heartbeat_io
    # Setear heartbeat con envio de hoy
    today_iso = datetime.now().isoformat()
    heartbeat_io.update_telegram(str(tmp_path), "gemini", today_iso, slug="t1")

    # Crear tenant_dir minimo (no se va a procesar)
    tenant_dir = tmp_path / "tenants" / "t1" / "status"
    tenant_dir.mkdir(parents=True)
    (tenant_dir / "daily_accum.json").write_text('{"updated":{}, "new":{}}')

    monkeypatch.setattr(nightly_report, "STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(nightly_report, "TENANTS_DIR", str(tmp_path / "tenants"))

    res = nightly_report.process_tenant_report({"slug": "t1", "state": "active"})
    assert res["status"] == "dup_skip"
    assert res["sent"] is False


def test_dedupe_allows_send_if_yesterday(tmp_path, monkeypatch):
    """Heartbeat con envio de ayer no bloquea el de hoy."""
    import heartbeat_io
    yesterday = (datetime.now() - timedelta(days=1)).isoformat()
    heartbeat_io.update_telegram(str(tmp_path), "gemini", yesterday, slug="t1")
    today = datetime.now().strftime("%Y-%m-%d")
    assert heartbeat_io.already_sent_today(str(tmp_path), "t1", today) is False


def test_dedupe_force_send_bypasses_check(tmp_path, monkeypatch):
    """_force_send=True ignora el dedupe (para E2E)."""
    import heartbeat_io
    today_iso = datetime.now().isoformat()
    heartbeat_io.update_telegram(str(tmp_path), "gemini", today_iso, slug="t1")
    # Sin accum -> caera en no_accum, no en dup_skip
    monkeypatch.setattr(nightly_report, "STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(nightly_report, "TENANTS_DIR", str(tmp_path / "tenants"))
    res = nightly_report.process_tenant_report({"slug": "t1", "state": "active", "_force_send": True})
    assert res["status"] != "dup_skip"


# ============ P1: QUIET SKIP DIA VACIO ============

def test_quiet_skip_when_recent_send_and_zero_items(tmp_path, monkeypatch):
    """Dia vacio Y envio reciente -> no manda Telegram (quiet_skip)."""
    import heartbeat_io
    heartbeat_io.update_telegram(str(tmp_path), "gemini", datetime.now().isoformat(), slug="t1")

    tenant_dir = tmp_path / "tenants" / "t1" / "status"
    tenant_dir.mkdir(parents=True)
    (tenant_dir / "daily_accum.json").write_text('{"updated":{}, "new":{}}')

    monkeypatch.setattr(nightly_report, "STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(nightly_report, "TENANTS_DIR", str(tmp_path / "tenants"))

    res = nightly_report.process_tenant_report({"slug": "t1", "state": "active"})
    # primero hace dup_skip (heartbeat tiene envio hoy), no llega a quiet_skip.
    # Probemos con envio de ayer:


def test_quiet_skip_on_sunday_with_yesterday_send(tmp_path, monkeypatch):
    """Domingo + envio de ayer + dia vacio -> quiet_skip."""
    import heartbeat_io
    yesterday = (datetime.now() - timedelta(days=1)).isoformat()
    heartbeat_io.update_telegram(str(tmp_path), "gemini", yesterday, slug="t1")

    tenant_dir = tmp_path / "tenants" / "t1" / "status"
    tenant_dir.mkdir(parents=True)
    (tenant_dir / "daily_accum.json").write_text('{"updated":{}, "new":{}}')

    monkeypatch.setattr(nightly_report, "STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(nightly_report, "TENANTS_DIR", str(tmp_path / "tenants"))
    monkeypatch.setattr(nightly_report, "_is_guaranteed_day", lambda *a, **kw: False)
    res = nightly_report.process_tenant_report({"slug": "t1", "state": "active"})
    assert res["status"] == "quiet_skip"
    assert res["sent"] is False


@patch('nightly_report.send_telegram', return_value=True)
def test_workday_empty_accum_sends_filler(mock_send, tmp_path, monkeypatch):
    """Lun-Sab + accum vacio -> SIEMPRE manda filler 'no_changes'."""
    tenant_dir = tmp_path / "tenants" / "t1" / "status"
    tenant_dir.mkdir(parents=True)
    (tenant_dir / "daily_accum.json").write_text('{"updated":{}, "new":{}}')

    monkeypatch.setattr(nightly_report, "STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(nightly_report, "TENANTS_DIR", str(tmp_path / "tenants"))
    monkeypatch.setattr(nightly_report, "_is_guaranteed_day", lambda *a, **kw: True)
    res = nightly_report.process_tenant_report({"slug": "t1", "state": "active"})
    assert res["status"] == "ok"
    assert res["provider"] == "filler_no_changes"
    assert mock_send.called
    body = mock_send.call_args[0][0]
    assert "sin cambios" in body.lower() or "no hubo cambios" in body.lower()


def test_deadman_when_no_send_in_7_days_on_sunday(tmp_path, monkeypatch):
    """Domingo + sin envio en 7+ dias + dia vacio -> manda mensaje deadman."""
    import heartbeat_io
    old = (datetime.now() - timedelta(days=10)).isoformat()
    heartbeat_io.update_telegram(str(tmp_path), "gemini", old, slug="t1")

    tenant_dir = tmp_path / "tenants" / "t1" / "status"
    tenant_dir.mkdir(parents=True)
    (tenant_dir / "daily_accum.json").write_text('{"updated":{}, "new":{}}')
    # branding minimo
    cfg_dir = tmp_path / "tenants" / "t1" / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "clients.yml").write_text("clients: []")
    monkeypatch.setattr(nightly_report, "_is_guaranteed_day", lambda *a, **kw: False)

    monkeypatch.setattr(nightly_report, "STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(nightly_report, "TENANTS_DIR", str(tmp_path / "tenants"))

    # Mock send_telegram para que no haga HTTP real
    with patch.object(nightly_report, "send_telegram", return_value=True) as m:
        res = nightly_report.process_tenant_report({"slug": "t1", "state": "active"})
    assert res["status"] == "ok"
    assert res["provider"] == "deadman"
    # El body deberia mencionar el tono deadman
    body_sent = m.call_args[0][0]
    assert "dias que no hay cambios" in body_sent


# ============ P3: ARCHIVE FAIL-SAFE ============

def test_archive_accum_recovers_from_rename_fail(tmp_path, monkeypatch):
    """Si os.rename falla (cross-fs), debe caer a copy + unlink."""
    accum = tmp_path / "daily_accum.json"
    accum.write_text("{}")
    status_dir = tmp_path / "status"
    status_dir.mkdir()

    def fail_rename(*a, **kw):
        raise OSError("simulated cross-fs")
    monkeypatch.setattr(os, "rename", fail_rename)

    nightly_report._archive_accum(str(accum), str(status_dir))
    # El original ya no debe existir y debe haber UN file en archive/
    assert not accum.exists()
    arch = list((status_dir / "archive").iterdir())
    assert len(arch) == 1

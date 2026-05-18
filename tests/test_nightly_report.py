"""Tests para nightly_report.py — cadena de fallback, prompt, plantilla, sanitización."""
import os
import sys
import json
import pytest
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
def test_process_tenant_report_skips_no_accum(mock_ai, mock_send, tmp_path, monkeypatch):
    monkeypatch.setattr(nightly_report, "TENANTS_DIR", str(tmp_path / "tenants"))
    res = nightly_report.process_tenant_report({"slug": "alpha", "state": "active"})
    assert res["status"] == "no_accum"
    assert not mock_send.called


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

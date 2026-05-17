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

def test_template_fallback_genera_mensaje_no_vacio():
    """Aun con listas vacias la plantilla devuelve mensaje util."""
    msg = nightly_report.render_template_fallback([], [], [], "17/05/2026")
    assert "Resumen del dia" in msg
    assert "17/05/2026" in msg
    assert "IA no disponible" in msg
    assert len(msg) > 50


def test_template_fallback_con_datos_reales():
    updated = [{"name": "x", "marca": "A"}] * 7
    top_brands = [("ARGENPLAS", 5), ("SCHNEIDER", 3)]
    top_hikes = [{"n": "Cable BS 3x16", "p": 5.8, "m": "ARGENPLAS"}]
    msg = nightly_report.render_template_fallback(updated, top_brands, top_hikes, "17/05/2026")
    assert "7 productos" in msg
    assert "ARGENPLAS" in msg
    assert "+5.8%" in msg
    assert "Cable BS 3x16" in msg


# ============ TONO DEL PROMPT ============

def test_prompt_menciona_persona_vendedor_pyme():
    """El prompt debe posicionar al LLM como asistente de vendedor PYME, no analista."""
    prompt = nightly_report.build_prompt([], [], [])
    p = prompt.lower()
    assert "vendedor" in p
    assert "pyme" in p or "ferreteria" in p
    # Si menciona "analista" debe ser en contexto negativo ("NO como analista")
    if "analista" in p:
        idx = p.index("analista")
        contexto_previo = p[max(0, idx - 30):idx]
        assert "no como" in contexto_previo or "no actues como" in contexto_previo, \
            f"'analista' aparece sin contexto negativo: ...{contexto_previo}analista..."


def test_prompt_prohibe_palabras_alarmistas():
    """El prompt debe instruir explicitamente a evitar palabras alarmistas."""
    prompt = nightly_report.build_prompt([], [], [])
    p = prompt.lower()
    # Si aparecen palabras de alarma, deben estar en contexto prohibitivo
    for palabra in ["critico", "alarmante", "advertencia", "riesgo"]:
        if palabra in p:
            # Debe estar precedida por "nunca uses" o similar
            assert "nunca uses" in p, f"'{palabra}' aparece sin contexto prohibitivo"


def test_prompt_pide_formato_html_acotado():
    """El prompt debe pedir HTML simple, max 1200 chars."""
    prompt = nightly_report.build_prompt([], [], [])
    assert "<b>" in prompt
    assert "1200" in prompt


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

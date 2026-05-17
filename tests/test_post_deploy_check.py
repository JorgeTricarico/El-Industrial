"""Tests para scripts/post_deploy_check.py — la red de seguridad que faltaba.

Cubre exactamente el bug del 19 dias: web publica sirviendo data congelada
mientras la Pi commiteaba data nueva.
"""
import gzip
import json
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
import post_deploy_check as pdc


@pytest.fixture
def fake_tenant(tmp_path, monkeypatch):
    """Arma un tenant con un .gz local y un _registry.yml apuntando a una URL fake."""
    tenants = tmp_path / "tenants"
    tenants.mkdir()
    (tenants / "_registry.yml").write_text("""
tenants:
  - slug: cliente-x
    state: active
    netlify_url: "https://fake-cliente-x.netlify.app"
""")
    t = tenants / "cliente-x"
    (t / "data").mkdir(parents=True)
    from datetime import datetime
    today_str = datetime.now().strftime("%y-%m-%d")
    fname = f"lista_precio_{today_str}_json_compres.gz"
    payload = [{"producto": "P1", "precio": "100"}, {"producto": "P2", "precio": "200"}]
    raw = gzip.compress(json.dumps(payload).encode("utf-8"))
    (t / "data" / fname).write_bytes(raw)

    monkeypatch.setattr(pdc, "TENANTS_DIR", str(tenants))
    monkeypatch.setattr(pdc, "REGISTRY", str(tenants / "_registry.yml"))
    return {"dir": t, "filename": fname, "raw_bytes": raw, "payload": payload}


def _mock_response(ok=True, status=200, content=b"", text=""):
    r = MagicMock(ok=ok, status_code=status, content=content, text=text or content.decode("utf-8", errors="replace"))
    return r


@patch.object(pdc.requests, "get")
def test_todo_ok_no_problems(mock_get, fake_tenant):
    """Web publica devuelve el mismo pointer y bytes que el local -> sin alerta."""
    def by_url(url, **kw):
        if url.endswith("latest-json-filename.txt"):
            return _mock_response(content=("data/" + fake_tenant["filename"]).encode())
        if url.endswith(".gz"):
            return _mock_response(content=fake_tenant["raw_bytes"])
        return _mock_response(ok=False, status=404)
    mock_get.side_effect = by_url
    code = pdc.main()
    assert code == 0


@patch.object(pdc.requests, "get")
def test_pointer_publico_desincronizado_alerta(mock_get, fake_tenant):
    """REPRODUCE EL BUG DEL 19 DIAS: web sirve otro pointer.
    Es exactamente lo que pasaba con el-industrial.netlify.app antes del fix.
    """
    def by_url(url, **kw):
        if url.endswith("latest-json-filename.txt"):
            return _mock_response(content=b"data/lista_precio_26-04-26_json_compres.gz")
        return _mock_response(ok=False, status=404)
    mock_get.side_effect = by_url
    with patch.object(pdc, "send_alert") as alert:
        code = pdc.main()
    assert code != 0, "post-deploy debe fallar si la web sirve data vieja"
    assert alert.called, "debe mandar Telegram cuando el pointer no matchea"
    msg = alert.call_args[0][0]
    assert any("DEPLOY NO LLEGO" in p or "se desincronizo" in p for p in msg)


@patch.object(pdc.requests, "get")
def test_sitio_publico_caido_alerta(mock_get, fake_tenant):
    mock_get.return_value = _mock_response(ok=False, status=503, text="Service Unavailable")
    code = pdc.main()
    assert code != 0


@patch.object(pdc.requests, "get", side_effect=Exception("connection refused"))
def test_network_down_alerta(_mock, fake_tenant):
    code = pdc.main()
    assert code != 0


@patch.object(pdc.requests, "get")
def test_precios_difieren_alerta(mock_get, fake_tenant):
    """Mismo pointer, mismo filename, pero el contenido del .gz publico tiene
    precios distintos al local. Caso de cache stale o deploy intermedio fallido.
    """
    bad_payload = [{"producto": "P1", "precio": "999"}, {"producto": "P2", "precio": "888"}]
    bad_raw = gzip.compress(json.dumps(bad_payload).encode("utf-8"))

    def by_url(url, **kw):
        if url.endswith("latest-json-filename.txt"):
            return _mock_response(content=("data/" + fake_tenant["filename"]).encode())
        if url.endswith(".gz"):
            return _mock_response(content=bad_raw)
        return _mock_response(ok=False)
    mock_get.side_effect = by_url
    code = pdc.main()
    assert code != 0


@patch.object(pdc.requests, "get")
def test_tenant_inactive_se_skip(mock_get, tmp_path, monkeypatch):
    tenants = tmp_path / "tenants"
    tenants.mkdir()
    (tenants / "_registry.yml").write_text("""
tenants:
  - slug: pausado
    state: inactive
    netlify_url: "https://x.netlify.app"
""")
    monkeypatch.setattr(pdc, "TENANTS_DIR", str(tenants))
    monkeypatch.setattr(pdc, "REGISTRY", str(tenants / "_registry.yml"))
    code = pdc.main()
    assert code == 0
    assert not mock_get.called

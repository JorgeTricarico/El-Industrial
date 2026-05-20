"""Tests del retry/timeout configurable de BertualAPIClient.

Motivacion: el cloud_update_resort.yml depende de poder darle a Bertual mas
de 30s y reintentar para sortear la latencia desde GH runners. Si rompemos
ese contrato, el plan B del workflow deja de funcionar silenciosamente.
"""
import json
import os
import socket
import sys
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


@pytest.fixture
def bertual_client(monkeypatch):
    monkeypatch.setenv("BERTUAL_CUIT", "test_cuit")
    monkeypatch.setenv("BERTUAL_PASSWORD", "test_pwd")
    monkeypatch.setenv("BERTUAL_CLIENT_ID", "test_cid")
    # Limpiar overrides de timeout/retries de tests previos
    monkeypatch.delenv("BERTUAL_TIMEOUT", raising=False)
    monkeypatch.delenv("BERTUAL_RETRIES", raising=False)
    # Recargar el modulo asi DEFAULT_TIMEOUT/RETRIES toman los nuevos env
    import importlib
    import bertual_api
    importlib.reload(bertual_api)
    return bertual_api


def _fake_response(payload):
    """Construye un context manager fake compatible con urllib.urlopen."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.read.return_value = json.dumps(payload).encode()
    cm.getheader.return_value = None
    return cm


def test_default_timeout_is_30s(bertual_client):
    """Default conservador: 30s y 1 reintento. Cambios accidentales rompen perf."""
    client = bertual_client.BertualAPIClient()
    assert client.timeout == 30
    assert client.retries == 1


def test_timeout_overridable_via_env(monkeypatch):
    """El workflow cloud_update_resort exporta BERTUAL_TIMEOUT=90 y RETRIES=3.
    Si esos env vars dejan de leerse, el plan B del workflow se rompe."""
    monkeypatch.setenv("BERTUAL_CUIT", "x")
    monkeypatch.setenv("BERTUAL_PASSWORD", "x")
    monkeypatch.setenv("BERTUAL_CLIENT_ID", "x")
    monkeypatch.setenv("BERTUAL_TIMEOUT", "90")
    monkeypatch.setenv("BERTUAL_RETRIES", "3")
    import importlib
    import bertual_api
    importlib.reload(bertual_api)
    client = bertual_api.BertualAPIClient()
    assert client.timeout == 90
    assert client.retries == 3


def test_login_retries_on_timeout(bertual_client):
    """Si el primer intento timeout-ea, reintenta hasta agotar retries."""
    client = bertual_client.BertualAPIClient(retries=2)
    call_count = {"n": 0}

    def fake_urlopen(req, timeout=None):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise socket.timeout("simulated timeout")
        return _fake_response({"token": "abc123"})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
         patch("time.sleep"):  # no esperar el backoff en tests
        client.login()

    assert call_count["n"] == 3
    assert client.token == "abc123"


def test_login_raises_after_retries_exhausted(bertual_client):
    """Si todos los reintentos fallan, RuntimeError con el last error."""
    client = bertual_client.BertualAPIClient(retries=1)

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("network unreachable")

    with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
         patch("time.sleep"):
        with pytest.raises(RuntimeError, match=r"Bertual login fallo tras 2 intentos"):
            client.login()


def test_fetch_uses_configured_timeout(bertual_client):
    """El timeout configurado se pasa a cada urlopen."""
    client = bertual_client.BertualAPIClient(timeout=77)
    client.token = "preauth"

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["timeout"] = timeout
        return _fake_response({"data": []})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
         patch("time.sleep"):
        client.fetch_products()

    assert captured["timeout"] == 77

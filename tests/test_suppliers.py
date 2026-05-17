"""Tests para scripts/suppliers/."""
import os
import sys

import pytest

import suppliers
from suppliers.bertual import BertualSupplier
from suppliers.haedo import HaedoSupplier


def test_registry_has_known_suppliers():
    avail = suppliers.available()
    assert "Bertual" in avail
    assert "Electronica Haedo" in avail


def test_get_returns_instance():
    s = suppliers.get("Bertual")
    assert isinstance(s, BertualSupplier)
    assert s.name == "Bertual"


def test_get_unknown_raises():
    with pytest.raises(ValueError, match="no registrado"):
        suppliers.get("ProveedorInventado")


def test_bertual_required_creds_keys():
    """Si esto cambia, hay que actualizar SUPPLIER_REQUIRED_KEYS en system_audit."""
    assert set(BertualSupplier.required_creds) == {
        "BERTUAL_CUIT", "BERTUAL_PASSWORD", "BERTUAL_CLIENT_ID"
    }


def test_bertual_transform_pesos():
    s = BertualSupplier()
    raw = {
        "Articulo_Corto": "TORNILLO 1/4",
        "Articulo": "TORNILLO HEX 1/4 ZINCADO",
        "Descripcion": "Tornillo cabeza hex",
        "Familia": "BULONERIA",
        "Moneda": "PES",
        "Precio": 100.0,
    }
    out = s.transform_item(raw, {"markup": 0.5, "iva": 0.21})
    assert out["producto"] == "TORNILLO 1/4"
    assert out["marca"] == "BULONERIA"
    assert out["moneda"] == "$"
    # 100 * 1.21 * 1.5 = 181.50
    assert out["precio"] == "181.50"


def test_bertual_transform_dolares():
    s = BertualSupplier()
    raw = {"Articulo": "X", "Descripcion": "Y", "Moneda": "DOL", "Precio": 50.0}
    out = s.transform_item(raw, {"markup": 0.0, "iva": 0.0})
    assert out["moneda"] == "U$S"
    assert out["precio"] == "50.00"


def test_bertual_transform_moneda_desconocida_passthrough():
    s = BertualSupplier()
    raw = {"Articulo": "X", "Descripcion": "Y", "Moneda": "EUR", "Precio": 10.0}
    out = s.transform_item(raw, {})
    assert out["moneda"] == "EUR"


def test_bertual_transform_articulo_corto_priority():
    """Articulo_Corto pisa Articulo si esta presente."""
    s = BertualSupplier()
    raw = {"Articulo_Corto": "ALU 1/2", "Articulo": "ALU LARGO", "Descripcion": "d"}
    out = s.transform_item(raw, {})
    assert out["producto"] == "ALU 1/2"


def test_haedo_fetch_empty_stub():
    s = HaedoSupplier()
    assert s.fetch_products({}) == []


def test_haedo_transform_basic():
    s = HaedoSupplier()
    raw = {"codigo": "C1", "descripcion": "d", "marca": "Phillips", "precio_neto": 200}
    out = s.transform_item(raw, {"markup": 0.3, "iva": 0.21})
    assert out["producto"] == "C1"
    assert out["marca"] == "Phillips"
    # 200 * 1.21 * 1.3 = 314.60
    assert out["precio"] == "314.60"


def test_bertual_fetch_uses_creds(monkeypatch):
    """fetch_products debe pasar las creds del dict al cliente, no leer env."""
    captured = {}

    class FakeClient:
        def __init__(self, cuit=None, password=None, client_id=None, api_url=None):
            captured.update(cuit=cuit, password=password, client_id=client_id, api_url=api_url)

        def fetch_products(self):
            return [{"x": 1}]

    import bertual_api
    monkeypatch.setattr(bertual_api, "BertualAPIClient", FakeClient)
    s = BertualSupplier()
    out = s.fetch_products({
        "BERTUAL_CUIT": "111",
        "BERTUAL_PASSWORD": "p",
        "BERTUAL_CLIENT_ID": "c",
        "API_URL": "https://test/",
    })
    assert out == [{"x": 1}]
    assert captured["cuit"] == "111"
    assert captured["password"] == "p"
    assert captured["client_id"] == "c"
    assert captured["api_url"] == "https://test/"

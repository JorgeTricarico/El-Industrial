"""Tests para scripts/update_products.py (Fase 2B multi-tenant).

Algunos tests se movieron a test_suppliers.py (transform_item, calculate_price)
porque ahora viven en scripts/suppliers/bertual.py. Acá quedan los que son
especificos de update_products: heartbeat, accum, fetch_with_retries,
node_status, y los nuevos de process_tenant.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

import update_products
from suppliers.bertual import BertualSupplier


# ---------- SEGURIDAD: el invariante critico de precio ----------

def test_security_invariant_no_discounts_via_supplier():
    """TEST DE SEGURIDAD: el precio NUNCA debe llevar el resale_discount.
    transform_item solo aplica markup + iva. resale_discount es ajeno al pipeline.
    """
    s = BertualSupplier()
    raw = {"Precio": 1000, "Articulo_Corto": "GUARD-01", "Descripcion": "x", "Moneda": "PES"}
    out = s.transform_item(raw, {"markup": 0.50, "iva": 0.21, "resale_discount": 0.20})
    precio_final = float(out["precio"])
    assert precio_final == 1815.0, "El precio calculado no debe llevar descuento"
    assert precio_final != 1452.0, "Se detecto resale_discount aplicado al precio publico"


def test_security_price_never_crashes_on_zero():
    """Si Precio=0 por error del API, no debe crashear; el pipeline lo
    descartara por validacion superior pero la transformacion sigue funcionando."""
    s = BertualSupplier()
    raw = {"Precio": 0, "Moneda": "PES", "Articulo": "X", "Descripcion": "y"}
    out = s.transform_item(raw, {"markup": 0.5, "iva": 0.21})
    assert float(out["precio"]) == 0.0


# ---------- moneda mapping (BertualSupplier) ----------

def test_transform_moneda_mapping():
    s = BertualSupplier()
    cases = [
        ("PES", "$"), ("ARS", "$"),
        ("DOL", "U$S"), ("USD", "U$S"),
        ("EUR", "EUR"),
    ]
    for monStr, expected in cases:
        raw = {"Precio": 10, "Moneda": monStr, "Articulo": "X", "Descripcion": "y"}
        assert s.transform_item(raw, {})["moneda"] == expected


# ---------- node status ----------

@patch("subprocess.check_output")
def test_node_status_logic(mock_ping):
    import subprocess
    mock_ping.return_value = b"bytes"
    assert update_products.check_node_status("100.1.1.1") == "online"
    mock_ping.side_effect = subprocess.CalledProcessError(1, ["ping"])
    assert update_products.check_node_status("100.1.1.1") == "offline"
    mock_ping.side_effect = FileNotFoundError("ping no encontrado")
    assert update_products.check_node_status("100.1.1.1") == "offline"


# ---------- fetch_with_retries (nueva signature: supplier + creds) ----------

@patch("time.sleep")
def test_api_resilience_retries(mock_sleep):
    """Falla 2 veces, succeed la 3era."""
    supplier = MagicMock()
    supplier.name = "Fake"
    supplier.fetch_products.side_effect = [
        Exception("Timeout"),
        Exception("500"),
        [{"Precio": 100}] * 110,
    ]
    data, _ = update_products.fetch_with_retries(supplier, creds={})
    assert len(data) == 110
    assert supplier.fetch_products.call_count == 3


# ---------- accumulator ----------

def test_accumulator_robustness_corrupt_file(tmp_path):
    """Si el archivo esta corrupto, se inicializa nuevo."""
    accum_file = tmp_path / "daily_accum.json"
    accum_file.write_text("esto no es un json { {")
    new_changes = {"new": [{"code": "ABC", "name": "Test"}], "updated": []}
    update_products.update_accumulator(new_changes, str(tmp_path))
    with open(accum_file, "r") as f:
        data = json.load(f)
    assert "ABC" in data["new"]


def test_accumulator_merges_repeat_updates(tmp_path):
    """Si llega update sobre item ya updateado, gana el nuevo precio."""
    update_products.update_accumulator(
        {"new": [], "updated": [{"code": "X", "name": "x", "old": "10", "new": "12"}]},
        str(tmp_path),
    )
    update_products.update_accumulator(
        {"new": [], "updated": [{"code": "X", "name": "x", "old": "12", "new": "15"}]},
        str(tmp_path),
    )
    with open(tmp_path / "daily_accum.json") as f:
        data = json.load(f)
    assert data["updated"]["X"]["new"] == "15"


def test_heartbeat_robustness_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setattr(update_products, "STATUS_DIR", str(tmp_path))
    (tmp_path / "heartbeat.json").write_text("corrupto")
    update_products.update_heartbeat("test-node")
    assert (tmp_path / "heartbeat.json").exists()


# ---------- diff_items ----------

def test_diff_items_detects_new_and_updated():
    new_items = [
        {"producto": "A", "detalle": "a", "precio": "10.00"},
        {"producto": "B", "detalle": "b", "precio": "20.00"},
        {"producto": "C", "detalle": "c", "precio": "30.00"},  # nuevo
    ]
    old_data = {
        "A": {"producto": "A", "precio": "10.00"},  # sin cambio
        "B": {"producto": "B", "precio": "18.00"},  # update
    }
    changes = update_products.diff_items(new_items, old_data)
    assert len(changes["new"]) == 1
    assert changes["new"][0]["code"] == "C"
    assert len(changes["updated"]) == 1
    assert changes["updated"][0]["code"] == "B"
    assert changes["updated"][0]["new"] == "20.00"


# ---------- sanity_check_prices ----------

def test_sanity_check_reverts_huge_jumps():
    """Cambio +107420% se rechaza, precio queda como el viejo."""
    new_items = [{"producto": "X", "detalle": "x", "precio": "1075.20"}]
    old_data = {"X": {"producto": "X", "precio": "1.00"}}
    out, sus = update_products.sanity_check_prices(new_items, old_data, max_pct=50)
    assert out[0]["precio"] == "1.00", "deberia haberse revertido al viejo"
    assert len(sus) == 1
    assert sus[0]["code"] == "X"
    assert sus[0]["pct"] > 50


def test_sanity_check_allows_small_changes():
    """Cambios bajo el umbral pasan sin tocar."""
    new_items = [{"producto": "X", "detalle": "x", "precio": "110.00"}]
    old_data = {"X": {"producto": "X", "precio": "100.00"}}
    out, sus = update_products.sanity_check_prices(new_items, old_data, max_pct=50)
    assert out[0]["precio"] == "110.00"
    assert sus == []


def test_sanity_check_ignores_new_items():
    """Items que no estan en old_data no se chequean (no hay baseline)."""
    new_items = [{"producto": "NUEVO", "detalle": "x", "precio": "999999"}]
    old_data = {}
    out, sus = update_products.sanity_check_prices(new_items, old_data, max_pct=50)
    assert out[0]["precio"] == "999999"
    assert sus == []


def test_sanity_check_handles_zero_old_price():
    """Si old_price es 0, evita divide-by-zero y deja pasar el item."""
    new_items = [{"producto": "X", "detalle": "x", "precio": "100.00"}]
    old_data = {"X": {"producto": "X", "precio": "0.00"}}
    out, sus = update_products.sanity_check_prices(new_items, old_data, max_pct=50)
    assert out[0]["precio"] == "100.00"
    assert sus == []


def test_sanity_check_drops_when_unparseable():
    """Si el precio viejo no es parseable, ignora el item silenciosamente."""
    new_items = [{"producto": "X", "detalle": "x", "precio": "10.00"}]
    old_data = {"X": {"producto": "X", "precio": "no-es-numero"}}
    out, sus = update_products.sanity_check_prices(new_items, old_data, max_pct=50)
    assert sus == []


# ---------- process_tenant ----------

def test_process_tenant_skips_inactive():
    res = update_products.process_tenant({"slug": "x", "supplier": "Bertual", "state": "inactive"})
    assert res["status"].startswith("skip")


def test_process_tenant_unknown_supplier():
    res = update_products.process_tenant({
        "slug": "x", "supplier": "ProveedorInexistente", "state": "active",
    })
    assert res["status"] == "supplier_unknown"


def test_process_tenant_creds_missing(monkeypatch):
    """Si faltan creds requeridas, el tenant no se procesa."""
    monkeypatch.setenv("BERTUAL_CUIT", "")  # vacio
    monkeypatch.delenv("BERTUAL_PASSWORD", raising=False)
    monkeypatch.delenv("BERTUAL_CLIENT_ID", raising=False)
    # forzar reload de os.environ en la funcion
    res = update_products.process_tenant({
        "slug": "demo", "supplier": "Bertual", "state": "active",
    })
    assert res["status"] == "creds_missing"
    assert "BERTUAL" in res["error"]


def test_process_tenant_ok_end_to_end(tmp_path, monkeypatch):
    """Mock supplier que devuelve 150 items, process_tenant escribe gz + accum."""
    monkeypatch.setattr(update_products, "TENANTS_DIR", str(tmp_path / "tenants"))

    tenant_root = tmp_path / "tenants" / "alpha"
    (tenant_root / "config").mkdir(parents=True)
    (tenant_root / "config" / "config.json").write_text(
        json.dumps({"markup": 0.0, "iva": 0.0})
    )

    fake_supplier = MagicMock()
    fake_supplier.name = "Bertual"
    fake_supplier.required_creds = ()  # bypass creds check
    fake_supplier.fetch_products.return_value = [
        {"Precio": 10, "Articulo": f"P{i}", "Descripcion": "x", "Moneda": "PES"}
        for i in range(150)
    ]
    fake_supplier.transform_item.side_effect = lambda raw, cfg: {
        "producto": raw["Articulo"],
        "detalle": "x",
        "marca": "",
        "moneda": "$",
        "precio": "10.00",
    }
    monkeypatch.setattr(update_products.suppliers, "get", lambda _: fake_supplier)

    res = update_products.process_tenant({
        "slug": "alpha", "supplier": "Bertual", "state": "active",
    }, silent=False)
    assert res["status"] == "ok", res
    assert res["new"] == 150  # primera corrida, todos son nuevos
    # Verificar archivos en disco
    data_dir = tenant_root / "data"
    assert any(f.endswith(".gz") for f in os.listdir(data_dir))
    assert (tenant_root / "latest-json-filename.txt").exists()
    assert (tenant_root / "status" / "daily_accum.json").exists()


def test_main_no_registry(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(update_products, "REGISTRY", str(tmp_path / "no.yml"))
    monkeypatch.setattr(update_products, "STATUS_DIR", str(tmp_path / "status"))
    rc = update_products.main([])
    assert rc == 1


def test_load_tenant_creds_overrides_root(tmp_path, monkeypatch):
    monkeypatch.setattr(update_products, "TENANTS_DIR", str(tmp_path / "tenants"))
    monkeypatch.setenv("BERTUAL_CUIT", "ROOT_CUIT")
    tenant_dir = tmp_path / "tenants" / "alpha"
    tenant_dir.mkdir(parents=True)
    (tenant_dir / ".env").write_text("BERTUAL_CUIT=TENANT_CUIT\nBERTUAL_PASSWORD=secret\n")
    creds = update_products.load_tenant_creds("alpha")
    assert creds["BERTUAL_CUIT"] == "TENANT_CUIT"
    assert creds["BERTUAL_PASSWORD"] == "secret"


def test_load_tenant_creds_falls_back_to_root_env(tmp_path, monkeypatch):
    monkeypatch.setattr(update_products, "TENANTS_DIR", str(tmp_path / "tenants"))
    monkeypatch.setenv("BERTUAL_CUIT", "ROOT_CUIT")
    creds = update_products.load_tenant_creds("alpha")  # sin tenants/alpha/.env
    assert creds["BERTUAL_CUIT"] == "ROOT_CUIT"

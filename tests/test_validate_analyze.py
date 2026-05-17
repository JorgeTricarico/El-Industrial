"""Tests para validate_prices y analyze_prices tenant-aware (M1, 2026-05-17)."""
import gzip
import json
import os
import sys
from unittest.mock import MagicMock

import pytest

SCRIPT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPT_DIR)

import analyze_prices
import update_products as up
import validate_prices


def _write_gz(path, items):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(items, f)


def _stub_registry(monkeypatch, tmp_path, tenants):
    monkeypatch.setattr(up, "TENANTS_DIR", str(tmp_path / "tenants"))
    monkeypatch.setattr(validate_prices, "TENANTS_DIR", str(tmp_path / "tenants"))
    monkeypatch.setattr(analyze_prices, "TENANTS_DIR", str(tmp_path / "tenants"))
    monkeypatch.setattr(up, "load_registry", lambda: tenants)


# ---------- validate_prices ----------

def test_validate_picks_first_active_when_no_slug(monkeypatch, tmp_path, capsys):
    _stub_registry(monkeypatch, tmp_path, [
        {"slug": "demo", "state": "testing", "supplier": "Bertual"},
        {"slug": "real", "state": "active", "supplier": "Bertual"},
    ])
    tenant_dir = tmp_path / "tenants" / "real"
    _write_gz(str(tenant_dir / "data" / "lista_precio_25-01-01.gz"),
              [{"producto": "A1", "precio": "100.00", "detalle": "x"}])
    (tenant_dir / "config").mkdir(parents=True, exist_ok=True)
    (tenant_dir / "config" / "config.json").write_text(json.dumps({"iva": 0, "markup": 0}))

    fake = MagicMock()
    fake.required_creds = ()
    fake.fetch_products.return_value = [{"Articulo": "A1", "Precio": 100, "Descripcion": "x", "Moneda": "PES"}]
    fake.transform_item.side_effect = lambda r, c: {"producto": r["Articulo"], "precio": "100.00", "detalle": "x"}
    monkeypatch.setattr(validate_prices.suppliers, "get", lambda _: fake)

    assert validate_prices.main([]) is True
    out = capsys.readouterr().out
    assert "real" in out
    assert "A1" in out


def test_validate_tenant_not_in_registry(monkeypatch, tmp_path):
    _stub_registry(monkeypatch, tmp_path, [{"slug": "x", "state": "active", "supplier": "Bertual"}])
    with pytest.raises(SystemExit):
        validate_prices.main(["--tenant", "ghost"])


def test_validate_detects_price_mismatch(monkeypatch, tmp_path, capsys):
    _stub_registry(monkeypatch, tmp_path, [{"slug": "t", "state": "active", "supplier": "Bertual"}])
    tenant_dir = tmp_path / "tenants" / "t"
    _write_gz(str(tenant_dir / "data" / "lista_precio_25-01-01.gz"),
              [{"producto": "A1", "precio": "999.00", "detalle": "x"}])
    (tenant_dir / "config").mkdir(parents=True, exist_ok=True)
    (tenant_dir / "config" / "config.json").write_text(json.dumps({"iva": 0, "markup": 0}))

    fake = MagicMock()
    fake.required_creds = ()
    fake.fetch_products.return_value = [{"Articulo": "A1"}]
    fake.transform_item.side_effect = lambda r, c: {"producto": "A1", "precio": "100.00", "detalle": "x"}
    monkeypatch.setattr(validate_prices.suppliers, "get", lambda _: fake)

    assert validate_prices.main(["--tenant", "t", "A1"]) is False
    assert "ERROR" in capsys.readouterr().out


# ---------- analyze_prices ----------

def test_analyze_auto_detects_last_two_gz(monkeypatch, tmp_path, capsys):
    _stub_registry(monkeypatch, tmp_path, [{"slug": "t", "state": "active", "supplier": "Bertual"}])
    tenant_dir = tmp_path / "tenants" / "t"
    _write_gz(str(tenant_dir / "data" / "lista_precio_25-01-01.gz"),
              [{"producto": "A1", "precio": "100.00", "detalle": "x", "marca": "M"}])
    _write_gz(str(tenant_dir / "data" / "lista_precio_25-02-01.gz"),
              [{"producto": "A1", "precio": "120.00", "detalle": "x", "marca": "M"}])

    monkeypatch.setattr(analyze_prices, "REPORTS_DIR", str(tmp_path / "reports"))

    assert analyze_prices.main(["--tenant", "t"]) is True
    # Reporte va a tmp_path/reports/t/
    reports = list((tmp_path / "reports" / "t").iterdir())
    assert any(p.name.startswith("analisis_precios_") and p.suffix == ".md" for p in reports)


def test_analyze_needs_two_snapshots(monkeypatch, tmp_path, capsys):
    _stub_registry(monkeypatch, tmp_path, [{"slug": "t", "state": "active", "supplier": "Bertual"}])
    tenant_dir = tmp_path / "tenants" / "t"
    _write_gz(str(tenant_dir / "data" / "lista_precio_25-01-01.gz"),
              [{"producto": "A1", "precio": "100.00", "detalle": "x"}])

    assert analyze_prices.main(["--tenant", "t"]) is False

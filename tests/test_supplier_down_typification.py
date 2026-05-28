import os
import json
import pytest
from unittest.mock import MagicMock, patch

import update_products

def test_fetch_with_retries_returns_error_msg():
    supplier = MagicMock()
    supplier.name = "TestSupplier"
    supplier.fetch_products.side_effect = Exception("HTTP Error 500: Internal Server Error")

    with patch("time.sleep"):  # Evita demoras reales en tests
        data, duration, err_msg = update_products.fetch_with_retries(supplier, creds={})
        assert data is None
        assert "HTTP Error 500" in err_msg

def test_process_tenant_typifies_supplier_down(tmp_path, monkeypatch):
    monkeypatch.setattr(update_products, "TENANTS_DIR", str(tmp_path / "tenants"))

    tenant_root = tmp_path / "tenants" / "beta"
    (tenant_root / "config").mkdir(parents=True)
    (tenant_root / "config" / "config.json").write_text(
        json.dumps({"markup": 0.0, "iva": 0.0})
    )

    fake_supplier = MagicMock()
    fake_supplier.name = "Bertual"
    fake_supplier.required_creds = ()  # bypass creds check
    
    # Simula fallo por connection timeout de base de datos
    fake_supplier.fetch_products.side_effect = RuntimeError("Bertual login fallo tras 3 intentos: HTTP Error 500: dial tcp4 181.164.35.234:8200: i/o timeout")
    monkeypatch.setattr(update_products.suppliers, "get", lambda _: fake_supplier)

    with patch("time.sleep"):
        res = update_products.process_tenant({
            "slug": "beta", "supplier": "Bertual", "state": "active",
        }, silent=True)

        assert res["status"] == "supplier_down"
        assert "i/o timeout" in res["error"]

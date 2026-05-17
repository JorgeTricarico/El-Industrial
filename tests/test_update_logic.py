"""Tests de la logica de precio + transform.

Originalmente vivian en update_products como funciones top-level
(calculate_price, transform_item). Fase 2B (2026-05-17) los movio a
scripts/suppliers/bertual.py para que cada tenant pueda tener su propio
adapter. Estos tests siguen verificando el mismo comportamiento via la
nueva interface, ademas de los tests mas exhaustivos en test_suppliers.py.
"""
from suppliers.bertual import BertualSupplier


def test_calculate_price_via_supplier():
    """Reemplaza test_calculate_price viejo. neto 100, iva 0.21, markup 0.30
    -> 100 * 1.21 * 1.30 = 157.30."""
    s = BertualSupplier()
    raw = {"Articulo": "X", "Descripcion": "y", "Moneda": "PES", "Precio": 100.0}
    out = s.transform_item(raw, {"iva": 0.21, "markup": 0.30})
    assert out["precio"] == "157.30"


def test_transform_item_uses_precio_not_precio_neto():
    """Articulo_Corto > Articulo, Familia trim, Moneda PES -> $.
    'Precio_Neto' debe ignorarse — la API usa 'Precio'."""
    s = BertualSupplier()
    raw = {
        "Precio": 100.0,
        "Precio_Neto": 70.0,
        "Articulo_Corto": "TEST01",
        "Descripcion": "Producto de Prueba",
        "Familia": " MARCA ",
        "Unidad": "UN",
        "Moneda": "PES",
    }
    out = s.transform_item(raw, {"iva": 0.0, "markup": 0.0})
    assert out["producto"] == "TEST01"
    assert out["moneda"] == "$"
    assert out["precio"] == "100.00"
    assert out["marca"] == "MARCA"

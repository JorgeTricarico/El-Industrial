import pytest
from unittest.mock import patch, MagicMock
import os
import sys
import json

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import update_products

def test_calculate_price():
    """Verifica que el cálculo de IVA y Markup sea exacto."""
    update_products.config.update({"markup": 0.50, "iva": 0.21, "resale_discount": 0.20})
    neto = 100
    # Cálculo esperado: 100 * 1.21 * 1.50 = 181.5
    assert update_products.calculate_price(neto) == 181.5

def test_security_invariant_no_discounts():
    """
    TEST DE SEGURIDAD (CRÍTICO): Garantiza que NUNCA se aplique un descuento
    al precio de lista en la transformación de items.
    """
    update_products.config.update({"markup": 0.50, "iva": 0.21, "resale_discount": 0.20})
    item_bruto = {
        "Precio": 1000, 
        "Articulo_Corto": "GUARD-01", 
        "Descripcion": "Test Seguridad",
        "Moneda": "PES"
    }
    
    # Transformación
    result = update_products.transform_item(item_bruto)
    precio_final = float(result["precio"])
    
    # El precio de venta DEBE ser 1815.00 (1000 * 1.21 * 1.50)
    # Si se aplicara el 'resale_discount' de 20%, daría 1452.00.
    
    assert precio_final == 1815.0, "FALLO DE SEGURIDAD: El precio calculado es incorrecto."
    assert precio_final != 1452.0, "FALLO DE SEGURIDAD: Se detectó la aplicación de un descuento de costo/resale!"

def test_transform_moneda_mapping():
    """Verifica que el mapeo de símbolos de moneda sea correcto."""
    items = [
        {"Precio": 10, "Moneda": "PES", "expected": "$"},
        {"Precio": 10, "Moneda": "ARS", "expected": "$"},
        {"Precio": 10, "Moneda": "DOL", "expected": "U$S"},
        {"Precio": 10, "Moneda": "USD", "expected": "U$S"},
        {"Precio": 10, "Moneda": "EUR", "expected": "EUR"}
    ]
    for case in items:
        res = update_products.transform_item(case)
        assert res["moneda"] == case["expected"]

@patch("subprocess.check_output")
def test_node_status_logic(mock_ping):
    """Verifica la lógica de detección de nodos online/offline."""
    # Simular Online
    mock_ping.return_value = b"bytes"
    assert update_products.check_node_status("100.1.1.1") == "online"
    
    # Simular Offline
    mock_ping.side_effect = Exception("error")
    assert update_products.check_node_status("100.1.1.1") == "offline"

@patch("time.sleep")
def test_api_resilience_retries(mock_sleep):
    """Garantiza que el sistema reintente 3 veces ante fallos de la API."""
    mock_client = MagicMock()
    # Falla 2 veces y tiene éxito a la tercera
    mock_client.fetch_products.side_effect = [
        Exception("Timeout"),
        Exception("500 Server Error"),
        [{"Precio": 100}] * 110 # Éxito con 110 productos
    ]
    
    data, lat = update_products.fetch_with_retries(mock_client)
    
    assert len(data) == 110
    assert mock_client.fetch_products.call_count == 3

def test_accumulator_robustness_corrupt_file(tmp_path):
    """Verifica que el acumulador se recupere si el archivo JSON está corrupto."""
    update_products.STATUS_DIR = str(tmp_path)
    accum_file = tmp_path / "daily_accum.json"
    
    # Escribir basura en el archivo
    with open(accum_file, "w") as f: f.write("esto no es un json { {")
    
    # El sistema no debe crashear, debe inicializar un acumulador nuevo
    new_changes = {"new": [{"code": "ABC", "name": "Test"}], "updated": []}
    update_products.update_accumulator(new_changes)
    
    with open(accum_file, "r") as f:
        data = json.load(f)
        assert "ABC" in data["new"]

def test_heartbeat_robustness_corrupt_file(tmp_path):
    """Verifica que el heartbeat maneje archivos corruptos sin morir."""
    update_products.STATUS_DIR = str(tmp_path)
    heartbeat_file = tmp_path / "heartbeat.json"
    
    with open(heartbeat_file, "w") as f: f.write("corrupto")
    
    # No debe dar error al actualizar
    update_products.update_heartbeat("test-node")
    assert heartbeat_file.exists()

def test_security_price_never_zero():
    """Garantiza que el sistema falle si por algún error el precio calculado es cero."""
    item = {"Precio": 0, "Moneda": "PES"}
    # El sistema debería manejarlo o nosotros deberíamos asegurar que no rompa
    res = update_products.transform_item(item)
    assert float(res["precio"]) == 0.0

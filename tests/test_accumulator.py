import os
import json
import pytest
from unittest.mock import patch
import sys

# Asegurar que el path de scripts esté disponible
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
import update_products

def test_accumulator_multiple_updates(tmp_path):
    """
    Valida que si un producto cambia de precio varias veces al día,
    el acumulador mantenga la trazabilidad correcta (el 'new' siempre sea el último).
    """
    update_products.STATUS_DIR = str(tmp_path)
    accum_path = os.path.join(update_products.STATUS_DIR, "daily_accum.json")
    
    # Simulación 1: Cambio inicial (100 -> 110)
    changes1 = {
        "new": [],
        "updated": [{"code": "P01", "name": "Prod 1", "old": "100.00", "new": "110.00"}]
    }
    update_products.update_accumulator(changes1)
    
    with open(accum_path, "r") as f:
        data = json.load(f)
        assert data["updated"]["P01"]["new"] == "110.00"
        assert data["updated"]["P01"]["old"] == "100.00"

    # Simulación 2: Segundo cambio el mismo día (110 -> 120)
    changes2 = {
        "new": [],
        "updated": [{"code": "P01", "name": "Prod 1", "old": "110.00", "new": "120.00"}]
    }
    update_products.update_accumulator(changes2)
    
    with open(accum_path, "r") as f:
        data = json.load(f)
        # El 'old' original debería mantenerse (opcional según diseño, 
        # pero aquí validamos que el 'new' sea el último)
        assert data["updated"]["P01"]["new"] == "120.00"

def test_accumulator_new_then_update(tmp_path):
    """
    Valida que si un producto es NUEVO y luego se ACTUALIZA el mismo día,
    se mantenga en la categoría 'new' con el precio final.
    """
    update_products.STATUS_DIR = str(tmp_path)
    accum_path = os.path.join(update_products.STATUS_DIR, "daily_accum.json")
    
    # 1. Aparece como nuevo a 500
    update_products.update_accumulator({"new": [{"code": "N01", "name": "Nuevo", "new": "500.00"}], "updated": []})
    
    # 2. Se actualiza a 550
    update_products.update_accumulator({"new": [], "updated": [{"code": "N01", "name": "Nuevo", "old": "500.00", "new": "550.00"}]})
    
    with open(accum_path, "r") as f:
        data = json.load(f)
        assert "N01" in data["new"]
        assert data["new"]["N01"]["new"] == "550.00"
        assert "N01" not in data["updated"]

def test_accumulator_corrupt_json_recovery(tmp_path):
    """
    Valida que si el archivo de acumulador se corrompe (ej. corte de luz),
    el sistema lo ignore y cree uno nuevo en lugar de crashear.
    """
    update_products.STATUS_DIR = str(tmp_path)
    accum_path = os.path.join(update_products.STATUS_DIR, "daily_accum.json")
    
    with open(accum_path, "w") as f:
        f.write("{ esta corrompido ...")
        
    # No debería lanzar excepción
    update_products.update_accumulator({"new": [{"code": "P1", "new": "10"}], "updated": []})
    
    assert os.path.exists(accum_path)
    with open(accum_path, "r") as f:
        data = json.load(f)
        assert "P1" in data["new"]

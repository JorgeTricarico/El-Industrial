import json
import gzip
import os

def test_json_integrity():
    latest_file = "latest-json-filename.txt"
    if not os.path.exists(latest_file):
        assert False, "No existe el archivo de puntero"
    
    with open(latest_file, "r") as f:
        path = f.read().strip()
    
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)
        assert len(data) > 100, "La base de datos parece demasiado pequeña"
        assert "producto" in data[0], "Falta columna crítica: producto"
        assert "precio" in data[0], "Falta columna crítica: precio"
        print(f"✅ Integridad verificada: {len(data)} productos OK.")

if __name__ == "__main__":
    try:
        test_json_integrity()
    except Exception as e:
        print(f"❌ FALLO DE INTEGRIDAD: {e}")
        exit(1)

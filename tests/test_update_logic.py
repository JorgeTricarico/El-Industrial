import unittest
import sys
import os

# Añadir el path de los scripts para poder importar
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from update_products import calculate_price, transform_item

class TestPriceLogic(unittest.TestCase):
    def test_calculate_price(self):
        # Si IVA es 0.21 y Markup es 0.30
        # neto 100 -> 100 * 1.21 * 1.30 = 157.3
        import update_products
        update_products.config["iva"] = 0.21
        update_products.config["markup"] = 0.30
        
        price = calculate_price(100)
        self.assertAlmostEqual(price, 157.3, places=2)

    def test_transform_item(self):
        import update_products
        update_products.config["iva"] = 0.0
        update_products.config["markup"] = 0.0
        
        # El test debe usar "Precio" y NO "Precio_Neto"
        api_item = {
            "Precio": 100.0,
            "Precio_Neto": 70.0, # Este debería ser ignorado según la nueva lógica
            "Articulo_Corto": "TEST01",
            "Descripcion": "Producto de Prueba",
            "Familia": " MARCA ",
            "Unidad": "UN",
            "Moneda": "PES"
        }
        
        transformed = transform_item(api_item)
        
        self.assertEqual(transformed["producto"], "TEST01")
        self.assertEqual(transformed["moneda"], "$")
        # Debe ser 100.00 porque debe tomar "Precio" y no "Precio_Neto"
        self.assertEqual(transformed["precio"], "100.00")
        self.assertEqual(transformed["marca"], "MARCA")

if __name__ == '__main__':
    unittest.main()

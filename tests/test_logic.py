import sys
import os
import unittest

# Agregar el directorio de scripts al path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from update_products import calculate_price, transform_item

class TestPriceLogic(unittest.TestCase):
    def test_calculate_price(self):
        # IVA=0.21, MARKUP=0.30 (segun config tipica)
        # 100 * 1.21 * 1.30 = 157.3
        import update_products
        update_products.config.update({"iva": 0.21, "markup": 0.30})
        res = calculate_price(100)
        self.assertGreater(res, 100)
        self.assertIsInstance(res, float)

    def test_transform_item(self):
        sample = {
            'Articulo_Corto': 'TEST01',
            'Precio_Neto': 100.0,
            'Descripcion': 'Producto de Prueba',
            'Moneda': 'PES',
            'Familia': ' TEST '
        }
        res = transform_item(sample)
        self.assertEqual(res['producto'], 'TEST01')
        self.assertEqual(res['moneda'], '$')
        self.assertEqual(res['marca'], 'TEST')

if __name__ == '__main__':
    unittest.main()

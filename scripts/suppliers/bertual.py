"""Adaptador Bertual: envuelve scripts/bertual_api.BertualAPIClient
para que cumpla la interface Supplier.

Las credenciales se pasan como dict en runtime (no se leen de env directo)
para que cada tenant pueda usar las suyas. Si el dict viene vacio en alguna
key, BertualAPIClient va a leer de os.environ como fallback (comportamiento
legacy de la clase).
"""
import os
import sys

from .base import Supplier

# bertual_api.py vive en scripts/, agregamos al path si hace falta
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)


class BertualSupplier(Supplier):
    name = "Bertual"
    required_creds = ("BERTUAL_CUIT", "BERTUAL_PASSWORD", "BERTUAL_CLIENT_ID")

    def fetch_products(self, creds):
        """Login + GET /precios. Devuelve lista de items raw del proveedor."""
        from bertual_api import BertualAPIClient  # import lazy
        client = BertualAPIClient(
            cuit=creds.get("BERTUAL_CUIT"),
            password=creds.get("BERTUAL_PASSWORD"),
            client_id=creds.get("BERTUAL_CLIENT_ID"),
            api_url=creds.get("API_URL"),
        )
        return client.fetch_products()

    def transform_item(self, raw, config):
        """Normaliza un item de Bertual al formato del front del tenant.
        Aplica markup + iva del config.
        """
        neto = raw.get("Precio", 0)
        markup = config.get("markup", 0.0)
        iva = config.get("iva", 0.0)
        precio = neto * (1 + iva) * (1 + markup)

        producto = raw.get("Articulo_Corto") or raw.get("Articulo")
        m_raw = str(raw.get("Moneda", "")).strip().upper()
        if m_raw in ("PES", "ARS"):
            moneda = "$"
        elif m_raw in ("DOL", "USD"):
            moneda = "U$S"
        else:
            moneda = m_raw  # EUR u otras

        return {
            "producto": producto,
            "detalle": raw.get("Descripcion"),
            "marca": (raw.get("Familia") or "").strip(),
            "moneda": moneda,
            "precio": "{:.2f}".format(precio),
        }

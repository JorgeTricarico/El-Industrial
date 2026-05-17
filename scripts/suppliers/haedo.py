"""Adaptador Electronica Haedo — STUB.

No tenemos API publica del proveedor todavia. Cuando se sume un cliente
real del rubro electrico, esta clase implementa el scraping/API/lo que sea.

Por ahora retorna lista vacia (asi no rompe update_products iterando sobre
tenants en state='testing'). El front del tenant demo-electricidad sigue
funcionando con su data mock espejada por sync_tenants.
"""
from .base import Supplier


class HaedoSupplier(Supplier):
    name = "Electronica Haedo"
    required_creds = ()  # sin API real todavia

    def fetch_products(self, creds):
        return []

    def transform_item(self, raw, config):
        # Misma forma que Bertual para consistencia del front
        neto = raw.get("precio_neto", 0)
        markup = config.get("markup", 0.0)
        iva = config.get("iva", 0.0)
        precio = neto * (1 + iva) * (1 + markup)
        return {
            "producto": raw.get("codigo") or raw.get("producto"),
            "detalle": raw.get("descripcion") or raw.get("detalle"),
            "marca": (raw.get("marca") or "").strip(),
            "moneda": raw.get("moneda", "$"),
            "precio": "{:.2f}".format(precio),
        }

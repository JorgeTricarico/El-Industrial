"""Adaptadores de proveedores mayoristas.

Cada supplier expone una interface comun (ver base.Supplier) para que
update_products.py pueda iterar tenants sin saber de Bertual/Haedo/etc.

Registry:
    suppliers.get("Bertual")    -> instancia lista para usar
    suppliers.get("Electronica Haedo")
"""
from .base import Supplier  # noqa: F401
from .bertual import BertualSupplier
from .haedo import HaedoSupplier


_REGISTRY = {
    "Bertual": BertualSupplier,
    "Electronica Haedo": HaedoSupplier,
}


def get(name):
    """Devuelve instancia del supplier o levanta ValueError si no existe."""
    cls = _REGISTRY.get(name)
    if not cls:
        raise ValueError(
            f"Supplier '{name}' no registrado. Disponibles: {list(_REGISTRY)}"
        )
    return cls()


def available():
    """Lista los nombres de suppliers registrados."""
    return list(_REGISTRY)

"""Interface base para adaptadores de proveedores."""


class Supplier:
    """Interface comun para adaptadores de proveedores mayoristas.

    Cada subclase implementa:
      - name (atributo de clase, str)
      - fetch_products(creds) -> list[dict]  # raw items del proveedor
      - transform_item(raw, config) -> dict  # item normalizado para el front

    transform_item recibe el config del tenant (markup/iva/etc) y devuelve
    un dict con las llaves que el JS del front espera:
      {"producto": str, "detalle": str, "marca": str, "moneda": str, "precio": str}

    creds es un dict con las credenciales del tenant. Las keys que cada
    supplier necesita estan documentadas en su clase (atributo `required_creds`).
    """

    name = "base"
    required_creds = ()  # tupla de nombres de env vars que se necesitan

    def fetch_products(self, creds):
        raise NotImplementedError

    def transform_item(self, raw, config):
        raise NotImplementedError

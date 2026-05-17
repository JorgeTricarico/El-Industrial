"""Aislamiento global de tests: ningun test debe escribir a status/ de prod.

Los scripts (nightly_report, update_products, healthcheck) tienen STATUS_DIR
como variable de modulo apuntando al status/ del repo. Sin este fixture,
log_metric() y update_accumulator() escriben metricas falsas al archivo real
y rompen la observabilidad cross-node.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))


@pytest.fixture(autouse=True)
def _isolate_status_dir(tmp_path, monkeypatch):
    # No creamos el directorio: cada script hace os.makedirs(..., exist_ok=True)
    # cuando lo necesita. Crear aca rompe tests que esperan un dir vacio.
    status_dir = tmp_path / "status"
    for mod_name in ("nightly_report", "update_products", "healthcheck"):
        if mod_name in sys.modules:
            monkeypatch.setattr(sys.modules[mod_name], "STATUS_DIR", str(status_dir), raising=False)
    yield status_dir

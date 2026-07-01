"""Aislamiento global de tests:
  - STATUS_DIR -> tmp_path para que log_metric() nunca contamine prod.
  - send_alert / send_telegram -> NO-OPS para que NINGUN test mande Telegram real.

INCIDENTE QUE ORIGINO EL SEGUNDO BLOQUE:
El 17/05 ~15:20 AR cada corrida del workflow 'Tests Pipeline' mandaba 5+
mensajes Telegram falsos al admin porque test_post_deploy_check ejecutaba
pdc.main() sin mockear send_alert. Cada test que probaba un escenario de
fallo (cliente-x sin sitename, etc.) terminaba mandando alerta real al chat
de produccion. Este conftest lo bloquea a nivel global.
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))

# Forzar import temprano de alert_throttle para que la fixture autouse de
# STATUS_DIR pueda monkeypatchearlo aunque ningun test lo importe directo.
try:
    import alert_throttle  # noqa: F401
except ImportError:
    pass


@pytest.fixture(autouse=True)
def _isolate_status_dir(tmp_path, monkeypatch):
    # No creamos el directorio: cada script hace os.makedirs(..., exist_ok=True)
    # cuando lo necesita. Crear aca rompe tests que esperan un dir vacio.
    status_dir = tmp_path / "status"
    for mod_name in ("nightly_report", "update_products", "healthcheck", "system_audit", "alert_throttle", "auto_fix"):
        if mod_name in sys.modules:
            monkeypatch.setattr(sys.modules[mod_name], "STATUS_DIR", str(status_dir), raising=False)
    yield status_dir


@pytest.fixture(autouse=True)
def _block_telegram_sends(request, monkeypatch):
    """Reemplaza send_alert/send_telegram con no-ops en cualquier modulo cargado.

    Tests que necesitan ejecutar el codigo real de send_alert (por ejemplo
    para validar el payload con requests.post mockeado) pueden marcar:
        @pytest.mark.allow_real_send
    En esos tests, este fixture es no-op.
    """
    if "allow_real_send" in request.keywords:
        yield
        return

    def _noop(*_a, **_kw):
        return False

    for mod_name in ("post_deploy_check", "healthcheck", "nightly_report", "system_audit", "auto_fix", "aiops_remediate"):
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            if hasattr(mod, "send_alert"):
                monkeypatch.setattr(mod, "send_alert", _noop, raising=False)
            if hasattr(mod, "send_telegram"):
                monkeypatch.setattr(mod, "send_telegram", _noop, raising=False)
    yield


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_real_send: ejecuta send_alert/send_telegram reales (requests.post igual debe estar mockeado).",
    )

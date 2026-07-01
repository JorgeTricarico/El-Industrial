"""Tests para auto_fix.py — break-glass autonomo.

Cubren la logica de decision should_run() (gate real de seguridad) y que main()
solo dispara el agente cuando corresponde y registra el intento (cooldown).
La invocacion real de `agy` y los pushes se mockean: NUNCA se corren en tests.
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
import auto_fix  # noqa: E402


def test_deshabilitado_por_default(monkeypatch):
    monkeypatch.delenv("AUTO_FIX_ENABLED", raising=False)
    ok, reason = auto_fix.should_run()
    assert ok is False
    assert "deshabilitado" in reason


def test_no_corre_si_update_reciente(monkeypatch):
    monkeypatch.setenv("AUTO_FIX_ENABLED", "1")
    monkeypatch.setattr(auto_fix, "hours_since_last_real_update", lambda: 5.0)
    ok, reason = auto_fix.should_run()
    assert ok is False
    assert "no grave" in reason


def test_corre_si_grave_y_sin_intento_previo(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_FIX_ENABLED", "1")
    monkeypatch.setattr(auto_fix, "STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(auto_fix, "hours_since_last_real_update", lambda: 100.0)
    ok, reason = auto_fix.should_run()
    assert ok is True
    assert "GRAVE" in reason


def test_no_corre_en_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_FIX_ENABLED", "1")
    monkeypatch.setattr(auto_fix, "STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(auto_fix, "hours_since_last_real_update", lambda: 100.0)
    auto_fix._save_state({"last_attempt_iso": (datetime.now() - timedelta(hours=2)).isoformat()})
    ok, reason = auto_fix.should_run()
    assert ok is False
    assert "cooldown" in reason


def test_corre_si_paso_el_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_FIX_ENABLED", "1")
    monkeypatch.setattr(auto_fix, "STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(auto_fix, "hours_since_last_real_update", lambda: 100.0)
    auto_fix._save_state({"last_attempt_iso": (datetime.now() - timedelta(hours=48)).isoformat()})
    ok, _ = auto_fix.should_run()
    assert ok is True


def test_no_corre_si_no_puede_determinar_edad(monkeypatch):
    monkeypatch.setenv("AUTO_FIX_ENABLED", "1")
    monkeypatch.setattr(auto_fix, "hours_since_last_real_update", lambda: None)
    ok, reason = auto_fix.should_run()
    assert ok is False
    assert "no se pudo determinar" in reason


def test_umbral_configurable_por_env(monkeypatch):
    monkeypatch.setenv("AUTO_FIX_ENABLED", "1")
    monkeypatch.setenv("AUTO_FIX_STALE_HOURS", "10")
    monkeypatch.setattr(auto_fix, "hours_since_last_real_update", lambda: 12.0)
    ok, _ = auto_fix.should_run()
    assert ok is True  # 12h supera el umbral bajado a 10h


def test_verdict_approved_parsing():
    """El parser de veredicto es conservador: aprueba solo si dice APROBADO
    sin RECHAZADO. Ante ambiguedad o vacio, NO aprueba (no pushear > pushear mal)."""
    assert auto_fix._verdict_approved("APROBADO: el fix ataca la causa raiz.") is True
    assert auto_fix._verdict_approved("aprobado, cambio minimo y correcto") is True
    assert auto_fix._verdict_approved("RECHAZADO: toca .env, peligroso.") is False
    assert auto_fix._verdict_approved("No lo daria por APROBADO: RECHAZADO.") is False
    assert auto_fix._verdict_approved("") is False
    assert auto_fix._verdict_approved("el cambio parece razonable") is False


def test_hours_since_last_real_update_parsea_git(monkeypatch):
    """Parsea el %cI del ultimo commit 'Actualizacion automatica' de origin/main."""
    iso = (datetime.now() - timedelta(hours=50)).isoformat()
    monkeypatch.setattr(auto_fix.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(auto_fix.subprocess, "check_output", lambda *a, **k: iso.encode())
    hrs = auto_fix.hours_since_last_real_update()
    assert hrs is not None and 49 < hrs < 51


def test_hours_since_none_si_no_hay_commits(monkeypatch):
    """Sin commits de update (output vacio) -> None (no dispara auto-fix)."""
    monkeypatch.setattr(auto_fix.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(auto_fix.subprocess, "check_output", lambda *a, **k: b"")
    assert auto_fix.hours_since_last_real_update() is None


def test_main_no_dispara_agente_si_no_corresponde(monkeypatch):
    """main() con should_run False NO debe invocar run_autofix."""
    monkeypatch.delenv("AUTO_FIX_ENABLED", raising=False)
    called = {"n": 0}

    def _fake_run(reason):
        called["n"] += 1
        return {"outcome": "pushed", "detail": ""}

    monkeypatch.setattr(auto_fix, "run_autofix", _fake_run)
    assert auto_fix.main() == 0
    assert called["n"] == 0


# --- Pipeline run_autofix con driver FALSO: prueba la garantia de seguridad ---
# (que verify_rejected y tests_failed BLOQUEEN el push). Sin git/agy/subprocess.

class FakeDriver:
    def __init__(self, agent_outputs, changed=True, tests_pass=True, push_ok=True):
        self._agent_outputs = list(agent_outputs)
        self._i = 0
        self.changed = changed
        self.tests_pass = tests_pass
        self.push_ok = push_ok
        self.calls = []

    def setup(self):
        self.calls.append("setup")
        return "BASESHA"

    def run_agent(self, prompt):
        self.calls.append("run_agent")
        out = self._agent_outputs[self._i]
        self._i += 1
        return out

    def discard_changes(self, sha):
        self.calls.append("discard")

    def has_changes(self, base_sha):
        self.calls.append("has_changes")
        return self.changed

    def commit_all(self, msg):
        self.calls.append("commit")
        return "FIXSHA"

    def get_diff(self, sha):
        return "diff --stat\n+algo"

    def run_tests(self):
        self.calls.append("run_tests")
        return self.tests_pass, "pytest output"

    def push(self, sha):
        self.calls.append("push")
        return self.push_ok, "push output"

    def summary(self, base_sha, fix_sha):
        return "abc1234 fix(auto): ..."

    def cleanup(self):
        self.calls.append("cleanup")


def test_pipeline_pushea_solo_si_aprueba_y_tests_verdes():
    """Happy path: fix con cambios + APROBADO + tests verdes -> push."""
    drv = FakeDriver(agent_outputs=["diagnostico...", "fix aplicado", "APROBADO: correcto"])
    res = auto_fix.run_autofix("grave", driver=drv)
    assert res["outcome"] == "pushed"
    assert "push" in drv.calls
    assert "cleanup" in drv.calls  # siempre limpia


def test_pipeline_NO_pushea_si_verificador_rechaza():
    """GARANTIA: si el verificador RECHAZA, no se corren tests ni se pushea."""
    drv = FakeDriver(agent_outputs=["diagnostico...", "fix aplicado", "RECHAZADO: rompe el deploy"])
    res = auto_fix.run_autofix("grave", driver=drv)
    assert res["outcome"] == "verify_rejected"
    assert "push" not in drv.calls
    assert "run_tests" not in drv.calls  # ni siquiera llega al gate de tests
    assert "cleanup" in drv.calls


def test_pipeline_NO_pushea_si_tests_fallan():
    """GARANTIA: verificador aprueba pero pytest falla -> NO se pushea."""
    drv = FakeDriver(agent_outputs=["diagnostico...", "fix aplicado", "APROBADO"], tests_pass=False)
    res = auto_fix.run_autofix("grave", driver=drv)
    assert res["outcome"] == "tests_failed"
    assert "run_tests" in drv.calls
    assert "push" not in drv.calls
    assert "cleanup" in drv.calls


def test_pipeline_no_change_si_diagnostico_sin_fix():
    """Si el diagnostico dice SIN FIX APLICABLE, no corre fix/verify/tests/push."""
    drv = FakeDriver(agent_outputs=["Diagnostico: SIN FIX APLICABLE, Bertual caido del lado del proveedor."])
    res = auto_fix.run_autofix("grave", driver=drv)
    assert res["outcome"] == "no_change"
    assert drv.calls.count("run_agent") == 1  # solo el diagnostico
    assert "push" not in drv.calls


def test_pipeline_no_change_si_fix_no_toca_nada():
    """Si el agente de fix no dejo cambios, no hay nada que verificar ni pushear."""
    drv = FakeDriver(agent_outputs=["diagnostico...", "no hice nada"], changed=False)
    res = auto_fix.run_autofix("grave", driver=drv)
    assert res["outcome"] == "no_change"
    assert "run_tests" not in drv.calls
    assert "push" not in drv.calls


def test_pipeline_push_failed_se_reporta():
    """Si el push falla (main avanzo, etc), outcome push_failed."""
    drv = FakeDriver(agent_outputs=["diag", "fix", "APROBADO"], push_ok=False)
    res = auto_fix.run_autofix("grave", driver=drv)
    assert res["outcome"] == "push_failed"
    assert "push" in drv.calls


def test_pipeline_cleanup_aunque_haya_excepcion():
    """El driver siempre se limpia, incluso si una etapa explota."""
    class Boom(FakeDriver):
        def run_agent(self, prompt):
            raise RuntimeError("boom")

    drv = Boom(agent_outputs=[])
    res = auto_fix.run_autofix("grave", driver=drv)
    assert res["outcome"] == "error"
    assert "cleanup" in drv.calls


def test_main_dispara_agente_y_registra_cooldown(tmp_path, monkeypatch):
    """main() con should_run True: invoca run_autofix y deja el intento
    registrado, de modo que el proximo should_run entra en cooldown."""
    monkeypatch.setenv("AUTO_FIX_ENABLED", "1")
    monkeypatch.setattr(auto_fix, "STATUS_DIR", str(tmp_path))
    monkeypatch.setattr(auto_fix, "hours_since_last_real_update", lambda: 100.0)
    called = {"n": 0}

    def _fake_run(reason):
        called["n"] += 1
        return {"outcome": "no_change", "detail": ""}

    monkeypatch.setattr(auto_fix, "run_autofix", _fake_run)
    assert auto_fix.main() == 0
    assert called["n"] == 1
    assert "last_attempt_iso" in auto_fix._load_state()
    ok, reason = auto_fix.should_run()
    assert ok is False and "cooldown" in reason

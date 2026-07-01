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

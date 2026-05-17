"""Tests para scripts/clients.py — broadcast multi-cliente."""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
import clients  # noqa: E402


def write_yaml(tmp_path, content):
    p = tmp_path / "clients.yml"
    p.write_text(content)
    return str(p)


def test_legacy_fallback_si_no_existe_archivo(tmp_path):
    """Sin clients.yml -> usa el legacy_chat_id."""
    fake_path = str(tmp_path / "no_existe.yml")
    out = clients.recipients_for("report", legacy_chat_id="12345", path=fake_path)
    assert out == [("12345", "legacy_env_chat_id")]


def test_legacy_fallback_si_archivo_vacio(tmp_path):
    p = write_yaml(tmp_path, "clients: []\n")
    out = clients.recipients_for("report", legacy_chat_id="999", path=p)
    assert out == [("999", "legacy_env_chat_id")]


def test_sin_legacy_y_sin_archivo_da_lista_vacia(tmp_path):
    out = clients.recipients_for("report", legacy_chat_id=None, path=str(tmp_path / "x.yml"))
    assert out == []


def test_report_incluye_admin_y_client(tmp_path):
    p = write_yaml(tmp_path, """
clients:
  - name: dev
    telegram_chat_id: 1
    enabled: true
    role: admin
  - name: cliente1
    telegram_chat_id: 2
    enabled: true
    role: client
""")
    out = clients.recipients_for("report", path=p)
    ids = [cid for cid, _ in out]
    assert "1" in ids and "2" in ids


def test_alert_excluye_clientes_pagos(tmp_path):
    p = write_yaml(tmp_path, """
clients:
  - name: dev
    telegram_chat_id: 1
    enabled: true
    role: admin
  - name: cliente1
    telegram_chat_id: 2
    enabled: true
    role: client
""")
    out = clients.recipients_for("alert", path=p)
    ids = [cid for cid, _ in out]
    assert ids == ["1"], "alert solo debe ir a admins"


def test_enabled_false_se_ignora(tmp_path):
    p = write_yaml(tmp_path, """
clients:
  - name: pausado
    telegram_chat_id: 1
    enabled: false
    role: client
  - name: activo
    telegram_chat_id: 2
    enabled: true
    role: client
""")
    out = clients.recipients_for("report", path=p)
    ids = [cid for cid, _ in out]
    assert ids == ["2"]


def test_chat_id_cero_o_vacio_se_ignora(tmp_path):
    p = write_yaml(tmp_path, """
clients:
  - name: sin_id
    telegram_chat_id: 0
    enabled: true
    role: client
  - name: ok
    telegram_chat_id: 999
    enabled: true
    role: client
""")
    out = clients.recipients_for("report", path=p)
    assert [cid for cid, _ in out] == ["999"]


def test_role_invalido_se_ignora(tmp_path):
    p = write_yaml(tmp_path, """
clients:
  - name: typo
    telegram_chat_id: 1
    enabled: true
    role: superadmin
  - name: ok
    telegram_chat_id: 2
    enabled: true
    role: admin
""")
    out = clients.recipients_for("report", path=p)
    assert [cid for cid, _ in out] == ["2"]


def test_category_invalida_levanta_error():
    with pytest.raises(ValueError):
        clients.recipients_for("typo", legacy_chat_id="1")


def test_yaml_corrupto_no_explota(tmp_path):
    p = write_yaml(tmp_path, "esto: [no es: yaml valido ::: {{}}")
    out = clients.recipients_for("report", legacy_chat_id="legacy", path=p)
    # Cae al legacy porque load fallo
    assert out == [("legacy", "legacy_env_chat_id")]


def test_tech_chat_override_redirige_alertas(tmp_path, monkeypatch):
    """Si TELEGRAM_TECH_CHAT_ID esta seteado, las alertas van SOLO ahi."""
    p = write_yaml(tmp_path, """
clients:
  - name: dev
    telegram_chat_id: 111
    enabled: true
    role: admin
  - name: cliente_paga
    telegram_chat_id: 222
    enabled: true
    role: client
""")
    monkeypatch.setenv("TELEGRAM_TECH_CHAT_ID", "999_tech")
    out = clients.recipients_for("alert", path=p)
    assert out == [("999_tech", "tech_channel")], (
        "alert con override debe ir SOLO al canal tecnico, nunca al admin/client del yaml"
    )


def test_tech_chat_override_no_afecta_reportes(tmp_path, monkeypatch):
    """El override solo afecta alerts. Los reportes comerciales siguen igual."""
    p = write_yaml(tmp_path, """
clients:
  - name: cliente_paga
    telegram_chat_id: 222
    enabled: true
    role: client
""")
    monkeypatch.setenv("TELEGRAM_TECH_CHAT_ID", "999_tech")
    out = clients.recipients_for("report", path=p)
    assert [cid for cid, _ in out] == ["222"]


def test_tech_chat_vacio_no_overridea(tmp_path, monkeypatch):
    p = write_yaml(tmp_path, """
clients:
  - name: dev
    telegram_chat_id: 111
    enabled: true
    role: admin
""")
    monkeypatch.setenv("TELEGRAM_TECH_CHAT_ID", "   ")
    out = clients.recipients_for("alert", path=p)
    assert [cid for cid, _ in out] == ["111"]

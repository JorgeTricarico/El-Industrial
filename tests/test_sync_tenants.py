"""Tests para scripts/sync_tenants.py — copia de front + data mirror."""
import json
import os
import shutil
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
import sync_tenants  # noqa: E402


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """Arma un repo falso con front raiz, data raiz, y 2 tenants registrados."""
    # Front raiz
    (tmp_path / "index.html").write_text("<title>X</title>")
    (tmp_path / "style.css").write_text("body{}")
    (tmp_path / "tornillo.png").write_bytes(b"\x89PNG_fake_")
    js_dir = tmp_path / "js"
    js_dir.mkdir()
    (js_dir / "main.js").write_text("console.log('main')")
    (js_dir / "test.test.js").write_text("// no debe copiarse")
    modules = js_dir / "modules"
    modules.mkdir()
    (modules / "api.js").write_text("// api")

    # Data raiz
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "lista_precio_26-05-15_json_compres.gz").write_bytes(b"old")
    (data_dir / "lista_precio_26-05-17_json_compres.gz").write_bytes(b"latest")

    # Tenants
    tenants = tmp_path / "tenants"
    tenants.mkdir()
    (tenants / "_registry.yml").write_text("""
tenants:
  - slug: cliente-test
    state: testing
  - slug: cliente-active
    state: active
  - slug: cliente-pausado
    state: inactive
""")
    for s in ("cliente-test", "cliente-active", "cliente-pausado"):
        (tenants / s).mkdir()

    # Repatchear las constantes del modulo al tmp
    monkeypatch.setattr(sync_tenants, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(sync_tenants, "TENANTS_DIR", str(tenants))
    monkeypatch.setattr(sync_tenants, "REGISTRY", str(tenants / "_registry.yml"))
    return tmp_path


def test_copia_front_a_tenant(fake_repo):
    sync_tenants.main()
    t = fake_repo / "tenants" / "cliente-test"
    assert (t / "index.html").read_text() == "<title>X</title>"
    assert (t / "style.css").read_text() == "body{}"
    assert (t / "js" / "main.js").exists()
    assert (t / "js" / "modules" / "api.js").exists()


def test_excluye_tests_js_del_front(fake_repo):
    sync_tenants.main()
    t = fake_repo / "tenants" / "cliente-test"
    assert not (t / "js" / "test.test.js").exists(), "tests *.test.js no deben copiarse al front del cliente"


def test_inactive_tenant_no_se_toca(fake_repo):
    sync_tenants.main()
    t = fake_repo / "tenants" / "cliente-pausado"
    assert not (t / "index.html").exists(), "tenants inactive no deben recibir nada"


def test_testing_recibe_data_mirror(fake_repo):
    sync_tenants.main()
    t = fake_repo / "tenants" / "cliente-test"
    assert (t / "data" / "lista_precio_26-05-17_json_compres.gz").read_bytes() == b"latest"
    # Pointer apuntando al ultimo
    txt = (t / "latest-json-filename.txt").read_text().strip()
    assert "26-05-17" in txt
    js = json.loads((t / "latest-json-filename.json").read_text())
    assert "26-05-17" in js["filename"]


def test_testing_borra_gz_viejos(fake_repo):
    t = fake_repo / "tenants" / "cliente-test"
    (t / "data").mkdir()
    # Pre-existing old gz
    (t / "data" / "lista_precio_26-04-01_json_compres.gz").write_bytes(b"viejo")
    sync_tenants.main()
    gz = list((t / "data").glob("*.gz"))
    assert len(gz) == 1, "solo debe quedar el ultimo .gz"
    assert "26-05-17" in gz[0].name


def test_active_tenant_recibe_mirror_durante_transicion(fake_repo):
    """Mientras update_products siga escribiendo a data/ raiz, los tenants 'active'
    tambien reciben mirror. Cuando ese script itere tenants, este test se invierte.
    """
    sync_tenants.main()
    t = fake_repo / "tenants" / "cliente-active"
    assert (t / "data" / "lista_precio_26-05-17_json_compres.gz").exists()

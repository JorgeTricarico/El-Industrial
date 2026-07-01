"""Tests del comportamiento de scripts/run_daily.sh en escenarios de cron.

Estos tests recrean el repo en un tmpdir, copian el script + sus dependencias,
y verifican que:
  - El dedup commit-marker IGNORA fillers/pulses ([GH-Actions] watchdog, chore: pulse).
  - El dedup commit-marker SI dispara dup_skip ante un "Actualizacion automatica" del dia.
  - Si git pull falla (conflicto), el script aborta con exit 2 y NO continua.

Motivacion: bug del 18-20 may 2026. La Pi quedo 2 dias sin actualizar precios
porque (a) un .gz untracked rompia el pull, y (b) un filler commit del cloud
con tag [run:YY-MM-DD] engañaba al dedup. Estos tests evitan regresion.
"""
import os
import shutil
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_DAILY = REPO_ROOT / "scripts" / "run_daily.sh"


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=check, env={**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t", "GIT_TERMINAL_PROMPT": "0"}
    )


@pytest.fixture
def fake_repo(tmp_path):
    """Setup: bare 'remote' + worktree 'local' con scripts copiados.

    Estructura minima que run_daily.sh espera:
      scripts/run_daily.sh, scripts/node_pulse.py (stub), scripts/refresh_heartbeat.py (stub)
      .env (vacio)
      status/, reports/
    """
    remote = tmp_path / "remote.git"
    local = tmp_path / "local"
    _git(tmp_path, "init", "--bare", str(remote), check=False)
    subprocess.run(["git", "init", str(local)], check=True, capture_output=True)
    _git(local, "config", "user.email", "t@t")
    _git(local, "config", "user.name", "T")
    _git(local, "remote", "add", "origin", str(remote))

    # Estructura
    (local / "scripts").mkdir()
    (local / "status").mkdir()
    (local / "reports").mkdir()
    shutil.copy(RUN_DAILY, local / "scripts" / "run_daily.sh")
    # Stubs minimos para los .py que run_daily.sh invoca
    (local / "scripts" / "node_pulse.py").write_text("import sys; sys.exit(0)\n")
    (local / "scripts" / "refresh_heartbeat.py").write_text("import sys; sys.exit(0)\n")
    (local / "scripts" / "update_products.py").write_text("import sys; print('[stub] update_products ran'); sys.exit(0)\n")
    (local / "scripts" / "sync_tenants.py").write_text("import sys; sys.exit(0)\n")
    (local / "scripts" / "post_deploy_check.py").write_text("import sys; sys.exit(0)\n")
    (local / "scripts" / "nightly_report.py").write_text("import sys; sys.exit(0)\n")
    (local / "scripts" / "aiops_remediate.py").write_text("import sys; sys.exit(0)\n")
    (local / ".env").write_text("")
    # No venv: el script cae al system python3 (que igualmente no se necesita
    # porque hacemos dup_skip antes de invocar update_products).

    (local / "README.md").write_text("test repo\n")
    _git(local, "add", ".")
    _git(local, "commit", "-m", "init")
    _git(local, "branch", "-M", "main")
    _git(local, "push", "-u", "origin", "main")
    return local


def _run_script(repo, extra_env=None):
    """Corre run_daily.sh en el repo de fixture. Devuelve CompletedProcess."""
    env = {**os.environ}
    env.pop("VIRTUAL_ENV", None)  # evitar interferencia
    env["EL_INDUSTRIAL_ROLE"] = "primary"  # forzar ruta primary (sin backup-check)
    env["HOME"] = str(repo)  # contener archivos temporales
    env["GIT_TERMINAL_PROMPT"] = "0"
    if extra_env:
        env.update(extra_env)
    # Lockfile aparte para no chocar con corridas reales en este host
    lock = repo / "test_lock"
    env["TMPDIR"] = str(repo)
    # El script usa /tmp/el_industrial.lock hardcodeado; lo limpiamos antes.
    if os.path.exists("/tmp/el_industrial.lock"):
        try:
            os.remove("/tmp/el_industrial.lock")
        except OSError:
            pass
    return subprocess.run(
        ["bash", str(repo / "scripts" / "run_daily.sh")],
        cwd=str(repo), env=env, capture_output=True, text=True
    )


def _today_tag():
    return f"[run:{datetime.now().strftime('%y-%m-%d')}]"


def test_dedup_skips_when_real_update_present(fake_repo):
    """Un commit 'Actualizacion automatica ... [run:hoy]' en origin debe disparar dup_skip."""
    tag = _today_tag()
    # Crear commit de update real en origin
    (fake_repo / "data_marker").write_text("update\n")
    _git(fake_repo, "add", ".")
    _git(fake_repo, "commit", "-m", f"Actualizacion automatica de precios: hoy [primary] {tag} [skip ci]")
    _git(fake_repo, "push", "origin", "main")

    result = _run_script(fake_repo)
    assert "dup_skip" in result.stdout or "dup_skip" in result.stderr, \
        f"Esperaba dup_skip cuando ya hay update real hoy. stdout={result.stdout[-500:]}"
    assert result.returncode == 0


def test_dedup_ignores_filler_commits(fake_repo):
    """El bug del 19-may: un filler 'chore: watchdog failover envio' con [run:hoy]
    NO debe disparar dup_skip. El primary debe seguir corriendo."""
    tag = _today_tag()
    (fake_repo / "status" / "heartbeat.json").write_text('{"nodes": {}}')
    _git(fake_repo, "add", ".")
    _git(fake_repo, "commit", "-m", f"chore: watchdog failover envio [GH-Actions] {tag} [skip ci]")
    _git(fake_repo, "push", "origin", "main")

    result = _run_script(fake_repo)
    assert "dup_skip" not in result.stdout, \
        f"Filler engañó al dedup (regresion del bug 19-may). stdout={result.stdout[-500:]}"
    # El script deberia avanzar mas alla del dedup. Como tenemos stubs de
    # update_products + sync_tenants, deberia terminar OK o intentar push.
    assert "Activando entorno virtual" in result.stdout or "update_products" in result.stdout, \
        f"El script no avanzo despues del dedup. stdout={result.stdout[-500:]}"


def test_dedup_ignores_pulse_commits(fake_repo):
    """Pulses de heartbeat (chore: pulse <node> dup_skip) tampoco deben engañar."""
    tag = _today_tag()
    (fake_repo / "status" / "heartbeat.json").write_text('{"nodes": {}}')
    _git(fake_repo, "add", ".")
    _git(fake_repo, "commit", "-m", f"chore: pulse raspberrypi dup_skip {tag} [skip ci]")
    _git(fake_repo, "push", "origin", "main")

    result = _run_script(fake_repo)
    assert "dup_skip" not in result.stdout, \
        f"Pulse commit engaño al dedup. stdout={result.stdout[-500:]}"


def test_supplier_down_logs_aviso_not_critico(fake_repo):
    """supplier_down (exit 3) es esperado/manejado: debe loguear AVISO, no CRITICO.

    Regresion del ruido nocturno (fix 2026-07-01): un exit 3 se logueaba como
    'CRITICO: update_products fallo con codigo 3' — palabra alarmista para una
    condicion que el filler Lun-Sab ya cubre. Debe decir AVISO y salir 3."""
    (fake_repo / "scripts" / "update_products.py").write_text("import sys; sys.exit(3)\n")
    _git(fake_repo, "add", ".")
    _git(fake_repo, "commit", "-m", "stub: update_products exit 3 (supplier_down)")
    _git(fake_repo, "push", "origin", "main")

    result = _run_script(fake_repo)
    out = result.stdout + result.stderr
    assert "AVISO: proveedor no respondio" in out, \
        f"Esperaba wording AVISO para supplier_down. stdout={out[-500:]}"
    assert "CRITICO: update_products" not in out, \
        f"supplier_down NO debe loguear CRITICO. stdout={out[-500:]}"
    assert result.returncode == 3


def test_non_supplier_failure_still_critico(fake_repo):
    """Un fallo inesperado (exit != 3) SI debe seguir siendo CRITICO."""
    (fake_repo / "scripts" / "update_products.py").write_text("import sys; sys.exit(5)\n")
    _git(fake_repo, "add", ".")
    _git(fake_repo, "commit", "-m", "stub: update_products exit 5 (fallo inesperado)")
    _git(fake_repo, "push", "origin", "main")

    result = _run_script(fake_repo)
    out = result.stdout + result.stderr
    assert "CRITICO: update_products fallo con codigo 5" in out, \
        f"Fallo inesperado debe seguir siendo CRITICO. stdout={out[-500:]}"
    assert result.returncode == 5


def test_backup_dup_skip_cuando_primary_ya_genero(fake_repo):
    """Rama backup (antes sin cobertura): si el primary ya publico el archivo
    del dia en GitHub, el backup hace dup_skip y sale 0 SIN correr
    update_products. Es el escenario DESKTOP-MI43BOU como backup.

    Mockeamos curl (--head --fail al raw de GitHub) con un fake que sale 0."""
    fakebin = fake_repo / "fakebin"
    fakebin.mkdir()
    curl = fakebin / "curl"
    curl.write_text("#!/bin/bash\nexit 0\n")  # simula: el archivo YA existe
    curl.chmod(0o755)

    result = _run_script(fake_repo, extra_env={
        "EL_INDUSTRIAL_ROLE": "backup",
        "PATH": f"{fakebin}:{os.environ['PATH']}",
    })
    out = result.stdout + result.stderr
    assert result.returncode == 0
    assert "dup_skip" in out, f"backup debio hacer dup_skip. stdout={out[-500:]}"
    assert "update_products" not in out, \
        f"backup no debio llegar a update_products. stdout={out[-500:]}"


def test_backup_procede_si_primary_no_genero(fake_repo):
    """Si el archivo del dia NO existe en GitHub (curl --fail sale !=0), el
    backup NO hace dup_skip: procede como backup a generar precios."""
    fakebin = fake_repo / "fakebin"
    fakebin.mkdir()
    curl = fakebin / "curl"
    curl.write_text("#!/bin/bash\nexit 22\n")  # simula: 404, archivo no existe
    curl.chmod(0o755)

    result = _run_script(fake_repo, extra_env={
        "EL_INDUSTRIAL_ROLE": "backup",
        "PATH": f"{fakebin}:{os.environ['PATH']}",
    })
    out = result.stdout + result.stderr
    assert "No se encontro el archivo de hoy" in out, \
        f"backup debio proceder al no encontrar el archivo. stdout={out[-500:]}"


def test_pull_fail_aborts_with_exit_2(fake_repo):
    """Si git pull falla (ej. remote inalcanzable), abortar con exit 2.

    Causa raiz del bug 19-may: pull fallaba (por .gz untracked), el script
    seguia con codigo stale, leía commit-marker viejo y hacia dup_skip falso.
    Aqui simulamos "pull falla" apuntando origin a un path inexistente."""
    _git(fake_repo, "remote", "set-url", "origin", "/dev/null/no-existe.git")

    result = _run_script(fake_repo)
    assert result.returncode == 2, \
        f"Esperaba exit 2 en pull fail, vino {result.returncode}. stdout={result.stdout[-500:]}"
    assert "git pull fallo (no es problema de .gz untracked). Abortando." in result.stdout, \
        f"Esperaba mensaje 'git pull fallo (no es problema de .gz untracked). Abortando.'. stdout={result.stdout[-500:]}"
    # Y CRITICO: no debe haber llegado a dup_skip ni a update_products
    assert "dup_skip" not in result.stdout
    assert "update_products" not in result.stdout

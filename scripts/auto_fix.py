#!/usr/bin/env python3
"""Auto-fix de ultimo recurso (break-glass).

Cuando el sistema lleva DIAS sin actualizar precios (fallo grave y sostenido,
no un hipo transitorio de Bertual), invoca al agente Antigravity (`agy`) para
que diagnostique y parchee el problema de forma autonoma.

Esto es un ARMA CARGADA: un agente con permisos auto-aprobados tocando el repo
que deploya a clientes reales. Por eso todos estos guardrails son OBLIGATORIOS:

  - OPT-IN por nodo: solo corre si AUTO_FIX_ENABLED=1 en el .env del nodo.
    Default OFF. Habilitar en UN solo nodo estable (ideal: la Raspberry Pi).
  - Solo GRAVE: se dispara si pasaron >= AUTO_FIX_STALE_HOURS (default 72h =
    3 dias) desde el ultimo commit "Actualizacion automatica" en origin/main.
  - COOLDOWN: no reintenta si ya lo intento hace < AUTO_FIX_COOLDOWN_HOURS
    (default 24h). Evita un loop de agentes peleandose entre si.
  - AISLAMIENTO: el agente trabaja en un CLON temporal cuyo remoto `origin`
    apunta al repo LOCAL (no a GitHub). Asi NO puede pushear a prod por su
    cuenta aunque quiera. El working tree vivo del nodo NUNCA se toca.
  - GATE DE TESTS por el WRAPPER (no por el agente): cuando el agente termina,
    ESTE script corre `pytest tests/` en el clon. Solo si pasa, pushea a main.
    Si falla, descarta el clon y prod nunca se entera.
  - AUDITORIA: Telegram + metrics.jsonl en cada paso. El intento se registra
    ANTES de correr (para que el cooldown aplique aunque el proceso muera).

Se invoca desde healthcheck.main() (auto-gated) o a mano:
    AUTO_FIX_ENABLED=1 python3 scripts/auto_fix.py
"""
import os
import sys
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
STATUS_DIR = os.path.join(BASE_DIR, "status")

DEFAULT_STALE_HOURS = 72       # 3 dias sin update real = grave
DEFAULT_COOLDOWN_HOURS = 24    # 1 intento por dia como maximo
DEFAULT_AGENT_TIMEOUT = 900    # 15 min para el agente
DEFAULT_TESTS_TIMEOUT = 600    # 10 min para pytest


def _env_bool(name, default=False):
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name, default):
    try:
        return int((os.getenv(name) or "").strip())
    except (ValueError, AttributeError):
        return default


def is_enabled():
    return _env_bool("AUTO_FIX_ENABLED", False)


def _state_file():
    return os.path.join(STATUS_DIR, "auto_fix_state.json")


def send_telegram(text):
    """Aviso tecnico al admin. No-op si faltan creds (o mockeado en tests)."""
    token = os.getenv("TELEGRAM_TOKEN")
    chat = os.getenv("TELEGRAM_TECH_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        return True
    except Exception:
        return False


def log_metric(event, detail=""):
    os.makedirs(STATUS_DIR, exist_ok=True)
    entry = {
        "ts": datetime.now().isoformat(),
        "node": "auto_fix",
        "event": event,
        "detail": str(detail)[:500],
    }
    try:
        with open(os.path.join(STATUS_DIR, "metrics.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def hours_since_last_real_update():
    """Horas desde el ultimo commit 'Actualizacion automatica' en origin/main.

    Es la señal definitiva de "no se estan actualizando precios", independiente
    de la causa (Bertual caido vs bug nuestro). None si no se puede determinar.
    """
    try:
        subprocess.run(
            ["git", "-C", BASE_DIR, "fetch", "origin", "--quiet"],
            timeout=30, stderr=subprocess.DEVNULL, check=False,
        )
        out = subprocess.check_output(
            ["git", "-C", BASE_DIR, "log", "origin/main",
             "--grep=^Actualizacion automatica", "-1", "--format=%cI"],
            timeout=15, stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None
    if not out:
        return None
    try:
        dt = datetime.fromisoformat(out)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return (datetime.now() - dt).total_seconds() / 3600
    except (ValueError, TypeError):
        return None


def _load_state():
    try:
        with open(_state_file(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state):
    os.makedirs(STATUS_DIR, exist_ok=True)
    try:
        with open(_state_file(), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass


def should_run(now=None):
    """Decide si corresponde disparar el auto-fix. Devuelve (bool, reason)."""
    now = now or datetime.now()
    if not is_enabled():
        return False, "deshabilitado (AUTO_FIX_ENABLED != 1)"
    stale_h = _env_int("AUTO_FIX_STALE_HOURS", DEFAULT_STALE_HOURS)
    cooldown_h = _env_int("AUTO_FIX_COOLDOWN_HOURS", DEFAULT_COOLDOWN_HOURS)
    hrs = hours_since_last_real_update()
    if hrs is None:
        return False, "no se pudo determinar la antiguedad del ultimo update"
    if hrs < stale_h:
        return False, f"no grave: {hrs:.1f}h sin update (umbral {stale_h}h)"
    last_iso = _load_state().get("last_attempt_iso")
    if last_iso:
        try:
            last = datetime.fromisoformat(last_iso)
            elapsed = (now - last).total_seconds() / 3600
            if elapsed < cooldown_h:
                return False, f"en cooldown: ultimo intento hace {elapsed:.1f}h (umbral {cooldown_h}h)"
        except (ValueError, TypeError):
            pass
    return True, f"GRAVE: {hrs:.1f}h sin update real (umbral {stale_h}h)"


def prompt_diagnose(reason):
    return (
        "Sos el agente de DIAGNOSTICO del sistema 'El Industrial' (monitoreo de "
        "precios B2B para PyMEs). NO modifiques ningun archivo — solo diagnostica.\n\n"
        f"Motivo del disparo: {reason}. El sistema NO actualiza precios hace dias.\n\n"
        "Lee CLAUDE.md y SYSTEM_STATE.md (arquitectura + gaps conocidos), y "
        "reports/cron_log.txt y status/metrics.jsonl (ultimas lineas) para el "
        "estado real. Devolve un diagnostico CONCISO:\n"
        "1. Causa raiz mas probable.\n"
        "2. Archivo(s) y funcion(es) involucrados.\n"
        "3. Fix minimo propuesto (1-3 pasos). NO escribas codigo todavia.\n"
        "Si la causa es externa e inarreglable en codigo (ej: Bertual caido del "
        "lado del proveedor), decilo explicito: 'SIN FIX APLICABLE' + por que."
    )


def prompt_fix(diagnosis):
    return (
        "Sos el agente de FIX del sistema 'El Industrial'. Un agente de "
        "diagnostico produjo esto:\n---\n"
        f"{diagnosis}\n---\n\n"
        "Aplica el fix MINIMO y quirurgico que restaure la actualizacion de "
        "precios, siguiendo ese diagnostico. REGLAS DURAS:\n"
        "- NO toques .env ni ningun secreto. NO los imprimas.\n"
        "- Cambios acotados, no refactors. Respeta CLAUDE.md.\n"
        "- Si el diagnostico dice 'SIN FIX APLICABLE', NO inventes un cambio: "
        "no toques nada.\n"
        "- INCLUI un test de regresion en tests/ que falle SIN tu fix y pase "
        "CON el. Un fix sin test no vale (el gate de pytest es la unica red).\n"
        "- Commitea localmente con un mensaje claro. NO hagas push (un wrapper "
        "externo corre los tests y pushea solo si pasan)."
    )


def prompt_verify(diff):
    return (
        "Sos el agente VERIFICADOR ADVERSARIAL del sistema 'El Industrial'. Otro "
        "agente aplico este cambio para restaurar la actualizacion de precios:\n"
        f"---\n{diff}\n---\n\n"
        "Evalualo con ojo critico, tratando de REFUTARLO. NO modifiques nada. "
        "Considera: ¿ataca la causa raiz o es cosmetico? ¿es minimo? ¿puede romper "
        "el deploy, el dedup commit-marker, o los efectos externos (Regla #1)? "
        "¿toca secretos? ¿podria ocultar un outage real (Regla #2)? ¿el fix "
        "viene con un test de regresion que lo cubra? Si NO trae test, RECHAZALO.\n"
        "Respuesta: en la PRIMERA linea escribi EXACTAMENTE 'APROBADO' o "
        "'RECHAZADO', y despues una razon breve."
    )


def _verdict_approved(text):
    """True solo si el verificador aprobo sin rechazar. Conservador: ante duda,
    no aprueba (mejor no pushear que pushear un fix dudoso)."""
    up = (text or "").upper()
    return "APROBADO" in up and "RECHAZADO" not in up


def _run(cmd, cwd, timeout):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _run_agent(agy_bin, clone, prompt, timeout):
    r = _run(
        [agy_bin, "--print", "--dangerously-skip-permissions",
         "--add-dir", clone, "--print-timeout", f"{timeout}s", prompt],
        cwd=clone, timeout=timeout + 60,
    )
    return (r.stdout or "").strip()


class _AutoFixError(Exception):
    """Error de una etapa del pipeline con su outcome asociado."""
    def __init__(self, outcome, detail=""):
        super().__init__(f"{outcome}: {detail}")
        self.outcome = outcome
        self.detail = detail


def _fix_commit_msg():
    stamp = datetime.now().strftime("%y-%m-%d")
    return f"fix(auto): remediacion automatica {stamp} [run:{stamp}] [skip ci]"


class _GitDriver:
    """Encapsula TODO el I/O (git, agy, pytest, filesystem) del pipeline.

    Todas las operaciones con efecto viven aca. run_autofix() solo orquesta,
    asi la logica de seguridad (¿cuando se pushea?) se testea con un driver
    falso, sin tocar git ni subprocess reales.
    """

    def __init__(self):
        self.stage_timeout = _env_int("AUTO_FIX_AGENT_TIMEOUT", DEFAULT_AGENT_TIMEOUT)
        self.tests_timeout = _env_int("AUTO_FIX_TESTS_TIMEOUT", DEFAULT_TESTS_TIMEOUT)
        self.agy_bin = os.getenv("AUTO_FIX_AGENT_BIN") or os.path.expanduser("~/.local/bin/agy")
        if not os.path.exists(self.agy_bin):
            raise _AutoFixError("agent_missing", f"no existe {self.agy_bin}")
        self.workdir = tempfile.mkdtemp(prefix="el-industrial-autofix-")
        self.clone = os.path.join(self.workdir, "repo")

    def _co(self, *args):
        return subprocess.check_output(["git", "-C", self.clone, *args]).decode().strip()

    def _head(self):
        return self._co("rev-parse", "HEAD")

    def setup(self):
        """Clona el repo local y lo alinea a la ultima main de github. Devuelve base_sha.

        El clon tiene origin=repo local; github queda como remoto 'prod'. Asi
        ningun agente puede pushear a prod: solo el wrapper, via 'prod'.
        """
        github_url = subprocess.check_output(
            ["git", "-C", BASE_DIR, "config", "--get", "remote.origin.url"]
        ).decode().strip()
        r = _run(["git", "clone", "--quiet", BASE_DIR, self.clone], cwd=self.workdir, timeout=120)
        if r.returncode != 0:
            raise _AutoFixError("clone_failed", r.stderr[:300])
        subprocess.run(["git", "-C", self.clone, "remote", "add", "prod", github_url], check=False)
        fr = _run(["git", "-C", self.clone, "fetch", "prod", "--quiet"], cwd=self.clone, timeout=60)
        if fr.returncode == 0:
            subprocess.run(["git", "-C", self.clone, "reset", "--hard", "prod/main"], check=False)
        return self._head()

    def run_agent(self, prompt):
        return _run_agent(self.agy_bin, self.clone, prompt, self.stage_timeout)

    def discard_changes(self, sha):
        subprocess.run(["git", "-C", self.clone, "reset", "--hard", sha], check=False)
        subprocess.run(["git", "-C", self.clone, "clean", "-fd"], check=False)

    def has_changes(self, base_sha):
        dirty = self._co("status", "--porcelain")
        return self._head() != base_sha or bool(dirty)

    def commit_all(self, msg):
        subprocess.run(["git", "-C", self.clone, "add", "-A"], check=False)
        subprocess.run(["git", "-C", self.clone, "commit", "-q", "-m", msg], check=False)
        return self._head()

    def get_diff(self, sha):
        return subprocess.check_output(
            ["git", "-C", self.clone, "show", "--stat", "-p", sha]).decode()[:6000]

    def run_tests(self):
        pybin = os.path.join(BASE_DIR, "venv", "bin", "python")
        if not os.path.exists(pybin):
            pybin = "python3"
        tr = _run([pybin, "-m", "pytest", "tests/", "-q"], cwd=self.clone, timeout=self.tests_timeout)
        return tr.returncode == 0, (tr.stdout or "")

    def push(self, sha):
        pr = _run(["git", "-C", self.clone, "push", "prod", f"{sha}:main"],
                  cwd=self.clone, timeout=120)
        return pr.returncode == 0, (pr.stderr or "")

    def summary(self, base_sha, fix_sha):
        return subprocess.check_output(
            ["git", "-C", self.clone, "log", "--oneline", f"{base_sha}..{fix_sha}"]
        ).decode().strip()

    def cleanup(self):
        shutil.rmtree(self.workdir, ignore_errors=True)


def run_autofix(reason, driver=None):
    """Orquesta el pipeline de agentes especializados. Devuelve {outcome, detail}.

    Cadena: diagnostico -> fix -> verificacion adversarial -> gate de pytest
    (wrapper) -> push. Cada agente corre aislado con contexto fresco (menos
    alucinacion; el que verifica no es el que parcheo). Todo el I/O vive en el
    driver; ESTA funcion solo decide. GARANTIA DE SEGURIDAD, testeada: solo se
    llega a driver.push() si el verificador aprobo Y pytest paso.
    """
    try:
        drv = driver or _GitDriver()
    except _AutoFixError as e:
        return {"outcome": e.outcome, "detail": e.detail}
    try:
        base_sha = drv.setup()

        # STAGE 1 — DIAGNOSTICO (read-only). Descartamos lo que deje.
        diagnosis = drv.run_agent(prompt_diagnose(reason))
        drv.discard_changes(base_sha)
        if "SIN FIX APLICABLE" in diagnosis.upper():
            return {"outcome": "no_change", "detail": diagnosis[-500:]}

        # STAGE 2 — FIX. Si no cambio nada, no hay que pushear.
        drv.run_agent(prompt_fix(diagnosis))
        if not drv.has_changes(base_sha):
            return {"outcome": "no_change", "detail": diagnosis[-500:]}
        fix_sha = drv.commit_all(_fix_commit_msg())
        diff = drv.get_diff(fix_sha)

        # STAGE 3 — VERIFICACION ADVERSARIAL (contexto fresco).
        verdict = drv.run_agent(prompt_verify(diff))
        drv.discard_changes(fix_sha)  # descarta ediciones del verificador, conserva el fix
        if not _verdict_approved(verdict):
            return {"outcome": "verify_rejected", "detail": verdict[-500:]}

        # STAGE 4 — GATE DURO: pytest (lo corre el wrapper, no un agente).
        passed, out = drv.run_tests()
        if not passed:
            return {"outcome": "tests_failed", "detail": out[-800:]}

        # Verificador aprobo + tests verdes -> push ff a github.
        ok, out = drv.push(fix_sha)
        if not ok:
            return {"outcome": "push_failed", "detail": out[:300]}
        return {"outcome": "pushed", "detail": drv.summary(base_sha, fix_sha)}
    except _AutoFixError as e:
        return {"outcome": e.outcome, "detail": e.detail}
    except subprocess.TimeoutExpired as e:
        return {"outcome": "timeout", "detail": str(e)}
    except Exception as e:
        return {"outcome": "error", "detail": f"{type(e).__name__}: {e}"}
    finally:
        try:
            drv.cleanup()
        except Exception:
            pass


_OUTCOME_MSG = {
    "pushed": "✅ <b>Auto-fix APLICADO</b>\nTests verdes, pusheado a main. Los nodos lo pullean solos.\nCambios:\n<code>{detail}</code>",
    "tests_failed": "⚠️ <b>Auto-fix ROLLBACK</b>\nEl fix rompio pytest. Descartado, prod intacto. REVISAR A MANO.\n<code>{detail}</code>",
    "verify_rejected": "⚠️ <b>Auto-fix RECHAZADO por el verificador</b>\nEl agente adversarial no aprobo el fix. Descartado, prod intacto. REVISAR A MANO.\n<code>{detail}</code>",
    "no_change": "ℹ️ <b>Auto-fix sin cambios</b>\nEl agente no encontro un fix aplicable (probablemente Bertual caido del lado del proveedor). REVISAR A MANO.",
    "agent_missing": "❌ <b>Auto-fix</b>: el binario del agente no existe. {detail}",
    "clone_failed": "❌ <b>Auto-fix</b>: fallo el clon aislado. {detail}",
    "push_failed": "❌ <b>Auto-fix</b>: tests verdes pero el push fallo (¿main avanzo?). REVISAR A MANO.\n{detail}",
    "timeout": "❌ <b>Auto-fix</b>: timeout. {detail}",
    "error": "❌ <b>Auto-fix</b>: error inesperado. {detail}",
}


def _notify_outcome(res):
    tmpl = _OUTCOME_MSG.get(res.get("outcome"), "Auto-fix outcome: {detail}")
    send_telegram(tmpl.format(detail=res.get("detail", "")))


def main():
    ok, reason = should_run()
    log_metric("autofix_check", reason)
    print(f"[auto_fix] {reason}")
    if not ok:
        return 0
    # Registrar el intento ANTES de correr: si el proceso muere, el cooldown
    # igual aplica y no reintentamos en loop.
    _save_state({"last_attempt_iso": datetime.now().isoformat(), "reason": reason})
    send_telegram(
        "🚨 <b>Auto-fix (break-glass)</b>\n"
        f"Gatillo: {reason}\n"
        "Invocando agente Antigravity en clon aislado. Solo se pushea si pytest pasa."
    )
    res = run_autofix(reason)
    log_metric(f"autofix_{res.get('outcome', 'unknown')}", res.get("detail", ""))
    print(f"[auto_fix] outcome={res.get('outcome')}")
    _notify_outcome(res)
    return 0


if __name__ == "__main__":
    # Cargar .env SOLO al correr standalone (via cron), no al importar: hacerlo
    # en import cargaria el .env real de prod y romperia el aislamiento de los
    # tests (Regla #1). Cuando lo invoca healthcheck, este ya cargo el .env.
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(BASE_DIR, ".env"))
    except Exception:
        pass
    sys.exit(main())

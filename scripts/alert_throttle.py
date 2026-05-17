"""Rate-limit de alertas Telegram para evitar spam.

Si el mismo conjunto de problemas se dispara N veces en M minutos, se loggea
pero NO se manda Telegram repetido. Util para healthcheck que corre cada
15min y podria mandar la misma alerta hasta que el dev despierte.

Implementacion: archivo JSON `status/alert_throttle.json` con
  {hash(fingerprint): last_sent_iso}
El caller pasa una lista de strings (los problemas detectados) y este
modulo decide si tiene que silenciarse.

NO tiene efectos externos: solo lee/escribe disco bajo STATUS_DIR. El
contrato (CLAUDE.md regla #1) no aplica acá porque no manda Telegram
ni hace HTTP — pero tests deberian usar tmp_path igual.
"""
import hashlib
import json
import os
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
STATUS_DIR = os.path.join(BASE_DIR, "status")
DEFAULT_WINDOW_MIN = int(os.getenv("ALERT_THROTTLE_MIN", "30"))


def _fingerprint(problems):
    """Hash estable de un conjunto de problemas. Orden insensible (set)."""
    if not problems:
        return None
    key = "\n".join(sorted(str(p) for p in problems))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _throttle_path(status_dir=None):
    return os.path.join(status_dir or STATUS_DIR, "alert_throttle.json")


def _load_state(status_dir=None):
    path = _throttle_path(status_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state, status_dir=None):
    sd = status_dir or STATUS_DIR
    try:
        os.makedirs(sd, exist_ok=True)
        with open(_throttle_path(sd), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError:
        pass


def should_send(problems, window_min=None, status_dir=None, now=None):
    """Decide si esta tanda de problemas debe mandarse o esta throttled.

    Retorna (send: bool, reason: str). reason es informativo para logs.
    Si send=True, marca el envio en el state ANTES de retornar (caller no
    tiene que llamar nada mas). Si send=False, no toca el state.
    """
    window_min = window_min if window_min is not None else DEFAULT_WINDOW_MIN
    now = now or datetime.now()
    fp = _fingerprint(problems)
    if not fp:
        return (False, "sin_problemas")

    state = _load_state(status_dir)
    last_iso = state.get(fp)
    if last_iso:
        try:
            last = datetime.fromisoformat(last_iso)
            if now - last < timedelta(minutes=window_min):
                age_min = (now - last).total_seconds() / 60
                return (False, f"throttled ({age_min:.0f}min < {window_min}min)")
        except ValueError:
            pass

    state[fp] = now.isoformat()
    # GC: borra entradas mas viejas que 24h
    cutoff = now - timedelta(hours=24)
    state = {k: v for k, v in state.items()
             if _parse_iso_safe(v) is None or _parse_iso_safe(v) > cutoff}
    _save_state(state, status_dir)
    return (True, "first_or_window_passed")


def _parse_iso_safe(s):
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None

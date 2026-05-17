"""Helper centralizado de zona horaria.

TODOS los timestamps que se muestran al usuario (Telegram, cron_log, headers
de reportes, alertas) deben pasar por aca. Internamente el host puede correr
en cualquier TZ (la Pi y los runners de GH Actions corren en UTC), pero el
usuario los espera en hora de Buenos Aires (UTC-3, sin horario de verano).
"""
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
    AR = ZoneInfo("America/Argentina/Buenos_Aires")
except ImportError:
    # Fallback para Python < 3.9: offset fijo -3h (Argentina no usa DST desde 2009).
    from datetime import timezone, timedelta
    AR = timezone(timedelta(hours=-3))


def now_ar():
    """Devuelve datetime aware en America/Argentina/Buenos_Aires."""
    return datetime.now(tz=AR)


def now_ar_iso():
    """ISO8601 con offset (-03:00). Para logs estructurados (metrics.jsonl)."""
    return now_ar().isoformat()


def now_ar_human(fmt="%d/%m/%Y %H:%M"):
    """Cadena legible para humanos. Default '17/05/2026 11:15'."""
    return now_ar().strftime(fmt)

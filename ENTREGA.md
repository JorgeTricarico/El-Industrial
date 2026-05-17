# 📦 Checklist de entrega — Operación desatendida

Esta guía es para dejar el proyecto **funcionando solo** para un cliente PYME, con alertas que avisen si algo se rompe.

## 1. Secrets que deben estar configurados en GitHub

`Settings → Secrets and variables → Actions` del repo. Todos obligatorios:

| Secret | Origen | Cómo obtenerlo |
|---|---|---|
| `GEMINI_API_KEY` | Google AI Studio | https://aistudio.google.com/app/apikey |
| `CEREBRAS_API_KEY` | Cerebras Cloud | https://cloud.cerebras.ai (signup gratuito) |
| `SAMBANOVA_API_KEY` | SambaNova Cloud | https://cloud.sambanova.ai/apis |
| `TELEGRAM_TOKEN` | BotFather en Telegram | `/newbot` en @BotFather |
| `TELEGRAM_CHAT_ID` | El chat del cliente | `curl https://api.telegram.org/bot<TOKEN>/getUpdates` |
| `BERTUAL_CUIT` | El cliente | CUIT de la cuenta Bertual del PYME |
| `BERTUAL_PASSWORD` | El cliente | Password Bertual |
| `BERTUAL_CLIENT_ID` | El cliente | Client ID Bertual (suele ser el CUIT) |

**Validación pre-flight**: el workflow `failover.yml` y `fallback_sync.yml` tienen un step `Verify LLM secrets present` que falla si alguno está vacío. Si CI marca este step en rojo, la entrega no está completa.

Comando rápido para setear desde local:
```bash
gh secret set CEREBRAS_API_KEY  # te lo pide por stdin
gh secret set GROQ_API_KEY
# ... etc
```

## 2. Crones que deben estar registrados

### En la Raspberry Pi (`crontab -e`)

```cron
# Reporte diario nocturno (20:00 local)
0 20 * * * /home/jorge/El-Industrial/scripts/run_daily.sh

# Ingesta silenciosa cada 2 horas (telemetría)
0 8-22/2 * * * /home/jorge/El-Industrial/scripts/run_frequent.sh

# Healthcheck matinal — alerta si el reporte de anoche no salió
0 8 * * * cd /home/jorge/El-Industrial && ./venv/bin/python3 scripts/healthcheck.py >> reports/healthcheck.log 2>&1
```

### En el nodo Mint (mismo crontab)

```cron
0 20 * * * /home/jorge/El-Industrial/scripts/run_daily.sh
```

(El propio script detecta que es nodo secundario y se auto-skipea si Pi ya pusheó.)

### En GitHub Actions (ya configurado vía `.github/workflows/*.yml`)

| Workflow | Cron UTC | Cron Argentina | Propósito |
|---|---|---|---|
| `fallback_sync.yml` | 23:30 | 20:30 | Respaldo si Pi/Mint no pushearon |
| `failover.yml` | 10:00, 22:00 | 07:00, 19:00 | Watchdog: si heartbeat >24h activa failover |
| `dead_man_switch.yml` | 12:00 | 09:00 | Healthcheck independiente — alerta Telegram si el reporte de anoche no salió |
| `test_pipeline.yml` | en push/PR | — | Tests automáticos |

## ⚠️ Limitación arquitectural conocida (importante)

**La API de Bertual NO es accesible desde los runners de GitHub Actions** (timeout). Esto significa que el "cloud fallback" no puede regenerar la lista de precios — sólo puede avisar al cliente que la Pi no respondió.

Resilencia real:
- **Pi y Mint caen, GH Actions corre**: ❌ NO salva con lista nueva, solo manda mensaje "Pi sin contacto hoy".
- **Pi cae pero ya pusheó ayer**: ✅ Reporte con datos previos.
- **Los 3 LLMs caen**: ✅ Plantilla local.

Si esto se vuelve crítico, opciones futuras: pedir a Bertual whitelist de IPs GH (https://api.github.com/meta), o setup self-hosted runner en una VPS argentina, o tunnel Tailscale.

## 3. Sistema de alertas — qué dispara qué

| Síntoma | Detector | Cómo te enteras |
|---|---|---|
| Pi se apagó / sin internet | `failover.yml` watchdog | Telegram "Pi sin contacto hoy" (NO regenera lista) |
| Reporte Telegram no llegó anoche | `dead_man_switch.yml` 12:00 UTC | Telegram con `🔧 El Industrial — chequeo...` |
| API Bertual cayó 3 corridas seguidas | `healthcheck.py` matinal en Pi | Telegram con detalle |
| Los 3 LLMs caídos | `nightly_report.py` plantilla fallback | Cliente recibe mensaje "Reporte automático (IA no disponible hoy)" — no es alerta, es UX |
| Secrets faltantes en GH Actions | `Verify LLM secrets present` step | Workflow falla en rojo, GitHub te notifica por email |
| Tests rotos en un PR | `test_pipeline.yml` | Check rojo en el PR |
| Sitio Netlify caído | (no implementado) | **Recomendación**: UptimeRobot gratis → `https://el-industrial.netlify.app` cada 5 min |

## 4. Procedimiento de respuesta a incidentes

### Si llega alerta "Heartbeat viejo"
1. SSH a la Pi: `ssh jorge@100.112.235.98` (vía Tailscale).
2. `cd ~/El-Industrial && tail -50 reports/cron_log.txt`
3. Si el cron no corrió: `systemctl status cron` o ver `last reboot`.
4. Ejecutar manualmente: `bash scripts/run_daily.sh` y verificar.

### Si llega alerta "Telegram no se envió hace Xh"
1. Verificar que el bot no esté bloqueado por el cliente.
2. `gh secret list` para confirmar que los secrets siguen presentes.
3. Trigger manual: `gh workflow run fallback_sync.yml`.

### Si llega alerta "API Bertual falló"
1. Probar credenciales desde local: `./venv/bin/python3 scripts/test_endpoints.py`.
2. Si las credenciales están bien pero la API sigue caída → escalar al cliente que Bertual está down (no es tu problema).
3. Si las credenciales se vencieron → pedir nuevas al cliente y actualizar `.env` + GitHub Secrets.

### Si los 3 LLMs caen simultáneamente (raro)
- El cliente recibe la plantilla automática. No requiere intervención manual.
- Si quieres mejorar resilencia futura: agregar un 4to proveedor (Anthropic Claude Haiku, OpenRouter).

## 5. Validación end-to-end antes de cobrar el primer mes

Ejecutar este checklist manualmente:

```bash
# 1. Tests locales pasan
./venv/bin/python3 -m pytest tests/ -v

# 2. Sintaxis de scripts y workflows
for f in scripts/*.py; do python3 -m py_compile "$f"; done
for f in scripts/*.sh; do bash -n "$f"; done
python3 -c "import yaml; [yaml.safe_load(open(f)) for f in ['.github/workflows/failover.yml', '.github/workflows/fallback_sync.yml', '.github/workflows/dead_man_switch.yml', '.github/workflows/test_pipeline.yml']]"

# 3. Healthcheck devuelve OK
./venv/bin/python3 scripts/healthcheck.py

# 4. Sitio público responde
curl -sI https://el-industrial.netlify.app | head -1

# 5. Trigger manual del workflow de fallback (debe enviar Telegram)
gh workflow run fallback_sync.yml

# 6. Esperar 3 min y verificar que llegó el mensaje al chat del cliente

# 7. Verificar que el dead_man_switch se ejecuta correctamente
gh workflow run dead_man_switch.yml
```

Si los 7 pasos pasan, el sistema está listo para operar desatendido.

## 6. Costos mensuales reales (revisar cada 6 meses)

| Item | Costo proyectado | Alerta si supera |
|---|---|---|
| Gemini free tier | USD 0 | 15 RPM → 1 reporte/día = 30 RPM/mes ≪ límite |
| Cerebras free tier | USD 0 | 60 RPM día/30 RPD según plan — alcanza |
| SambaNova free tier | USD 0 | 60 RPM, Meta-Llama-3.3-70B-Instruct — alcanza |
| Netlify | USD 0 | 100 GB/mes free → cada cliente ~1 MB/día = 30 MB/mes |
| GH Actions | USD 0 | 2000 min/mes free; cada cliente usa ~10 min/mes |

**Cuando llegues a ~15 clientes** revisar el uso del free tier de cada API. Es posible que necesites pagar plan Pro de Gemini (~USD 50/mes para 1500 RPM).

## 7. Plan de contingencia comercial

| Escenario | Acción |
|---|---|
| Cliente reporta que no recibe Telegram pero todo se ve OK del lado tuyo | Verificar `last_telegram_iso` en `status/heartbeat.json`. Si dice que se mandó pero el cliente no lo ve → puede haberse bloqueado el bot. Mandar mensaje de prueba manual. |
| Cliente cambia su Telegram chat | Actualizar `TELEGRAM_CHAT_ID` secret en GH + `.env` en Pi + Mint. |
| Cliente cancela | Remover sus secrets (`gh secret delete ...`) o si es multi-tenant futuro, sacar su `tenants/<slug>.json`. |
| Bertual API cambia formato | Tests en `tests/test_update_products.py` detectarán la regresión si actualizas el fixture. Mantener fixture sintético en `tests/fixtures/`. |

## 8. Lo que falta para escalar a >1 cliente (Fase 3 del plan)

- Estructura `tenants/<slug>.json` por cliente (creds Bertual + chat Telegram + markup + branding).
- `scripts/nightly_report.py --tenant=<slug>` para iterar por cliente.
- `scripts/onboard_client.sh` para automatizar setup de un cliente nuevo (5 min en vez de 30).
- Landing page con captura de leads.
- Contrato + SLA (1 página).

Ver `/home/jorge/.claude/plans/revisa-por-completo-la-resilient-kernighan.md` para detalle.

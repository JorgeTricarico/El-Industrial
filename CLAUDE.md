# CLAUDE.md — Contrato para agentes y devs que tocan este repo

Este archivo es el contrato visible del repo. Lo lee Claude Code y debe leerlo
cualquier humano que entre a contribuir. Las reglas de abajo NO son guía;
son obligatorias. Romperlas ya nos costó horas y ensució produccion.

## Qué es este repo

SaaS de monitoreo de precios B2B para PyMEs argentinas. Arquitectura 3-nodos:

- **Raspberry Pi** (`100.112.235.98` via Tailscale): cron real, fetch a APIs
  de proveedores (Bertual, Haedo), genera data, push a GitHub.
- **Linux Mint** (laptop secundaria, Tailscale): nodo de respaldo + dev local.
- **GitHub Actions**: fallback si Pi/Mint caen, observabilidad, E2E semanal.

Cada cliente vive en `tenants/<slug>/` self-contained. `tenants/_registry.yml`
es la fuente de verdad. Cada tenant tiene su propio site Netlify deploy
**100% via API REST** (autodeploy git desactivado, ver `sync_tenants.py`).

## Regla #1 — No efectos externos por default

> **Cualquier función que escriba a disco persistente, haga HTTP a servicios
> externos, o mande mensaje a usuarios reales, tiene que ser NO-OP por
> default cuando se importa desde un proceso que no sea el runner de prod.**

Esto incluye:

- `send_telegram` / `send_alert` (mensajes a usuarios)
- `log_metric()` cuando escribe a `status/metrics.jsonl` de prod
- `requests.post` a Netlify API, Bertual API, Telegram API
- Escritura a `data/`, `reports/`, `status/`, `tenants/<slug>/data/`

El bug raíz que motiva esto se repitió **3 veces en una misma sesion** el
17-may-2026: tests escribiendo metrics falsos a prod, scripts ad-hoc en SSH
ensuciando metrics.jsonl real, y `test_post_deploy_check` mandando 5+
Telegrams falsos al admin con cosas como "cliente-x sin sitename" (cliente-x
era un fixture). Ver memoria `feedback_no_efectos_externos_default.md`.

### Cómo se implementa hoy

`tests/conftest.py` tiene dos fixtures `autouse=True` que cubren TODO test:

1. `_isolate_status_dir`: redirige `STATUS_DIR` de cada módulo a `tmp_path`.
2. `_block_telegram_sends`: reemplaza `send_alert`/`send_telegram` por no-ops.

Tests que necesitan ejecutar el código real (por ej. para validar el payload
con `requests.post` ya mockeado) marcan: `@pytest.mark.allow_real_send`.

### Módulos cubiertos por el bloqueo del conftest

Lista canonica al 2026-05-17 (mantener sincronizada al sumar módulos):

- `post_deploy_check` — `send_alert`
- `healthcheck` — `send_alert`, `STATUS_DIR`
- `nightly_report` — `send_telegram`, `STATUS_DIR`
- `update_products` — `STATUS_DIR`
- `sync_tenants` — (futuro: HTTP a Netlify, ver Regla #1bis)
- `system_audit` — `send_alert`
- `alert_throttle` — `STATUS_DIR`

### Cómo agregar un módulo nuevo con efectos externos

1. Implementás el módulo con `send_X` / `STATUS_DIR` / HTTP-cliente.
2. **Antes de mergear**, sumás el nombre del módulo al loop en
   `tests/conftest.py` (`_isolate_status_dir` y/o `_block_telegram_sends`).
3. Sumás el nombre a la lista de arriba.
4. Si el módulo hace HTTP a un servicio externo nuevo (no Telegram), mockeás
   `requests.post`/`requests.get` por default vía fixture autouse o factory.
5. Tests que **opt-in** al envío real usan el marker `allow_real_send`.

### Scripts ad-hoc (REPL, SSH, debug)

En cualquier script que NO sea el runner de prod (incluye debug en SSH a la
Pi y notebooks), si importás un módulo con efectos externos, **patchear
`send_X` antes de llamar nada**. Ejemplo:

```python
import nightly_report as nr
nr.send_telegram = lambda *a, **kw: print("[no-op send]", a)
nr.main()  # ahora seguro
```

## Regla #2 — Validar desde POV del cliente final

> **Antes de tests unitarios, tests E2E que cruzan la frontera del producto.
> Si el cliente final no lo ve correcto, todo lo demas es accesorio.**

Bug del 27-abr al 17-may 2026: `el-industrial.netlify.app` servia data del
26-abr durante 19 dias. Tests unitarios verdes, Pi commiteando data fresca,
healthcheck OK, Telegram nocturno OK. **Ningun test cruzaba "lo que el
sistema produce" → "lo que el cliente ve"**.

### Cómo se implementa hoy

- `scripts/post_deploy_check.py`: compara data local Pi ↔ web publica ↔
  Bertual API (3 niveles, tolerancia 1%, 10 precios random). Corre tras
  cada cron en la Pi.
- `tests/e2e/netlify_smoke.spec.js` + `.github/workflows/e2e_post_deploy.yml`:
  Playwright contra Netlify prod 2x/dia.
- `healthcheck.detect_public_site_stale`: cada nodo verifica fecha del
  filename publico.

### Cómo agregar una feature visible al cliente

1. **Primer test**: lo veo en el browser contra prod (E2E o smoke curl
   contra el dominio publico).
2. Despues los unitarios.

## Regla #3 — Defaults seguros en deploy

- **Netlify**: todos los sites tienen `stop_builds=true`. Deploys solo via
  API REST desde `sync_tenants.py`. Esto elimina el path "build Netlify"
  que puede romperse silenciosamente (causa raiz del bug del 19 dias).
- Cualquier site nuevo se crea con `cmd=''` y `dir='tenants/<slug>'`.
- `scripts/system_audit.py` chequea esto semanalmente (build_settings drift).

## Multi-tenancy (Fase 2B)

Cada cliente vive en `tenants/<slug>/` con su data, status, config y branding.
`tenants/_registry.yml` es la fuente de verdad: slug, state, netlify_site_id,
supplier.

Pipeline iterando tenants (sin compat con root `data/` desde M1, 2026-05-17):
- `update_products.py` itera tenants `state=active`. Para cada uno: carga
  supplier adapter desde `scripts/suppliers/`, credenciales (de
  `tenants/<slug>/.env` si existe, sino del `.env` raiz), config, fetcha,
  diff, escribe `tenants/<slug>/data/lista_precio_*.gz` + accum. **No
  escribe a `data/` ni a `status/daily_accum.json` del root.** El root
  `status/` queda solo para cosas globales (heartbeat, metrics.jsonl,
  alerts.jsonl).
- `nightly_report.py` itera tenants. Para cada uno: lee
  `tenants/<slug>/status/daily_accum.json`, manda al canal del tenant
  (`tenants/<slug>/config/clients.yml`).
- `validate_prices.py --tenant <slug>` (default: primer `active`) compara
  el `.gz` de `tenants/<slug>/data/` con lo que devuelve el supplier del
  tenant. Sin flag = primer active.
- `analyze_prices.py --tenant <slug>` (default: primer `active`) auto-detecta
  el último y anteúltimo `.gz` del tenant y genera reportes en
  `reports/<slug>/`.
- `scripts/suppliers/__init__.py` registry: `Bertual`, `Electronica Haedo`.

### Cómo agregar un proveedor nuevo

1. Crear `scripts/suppliers/<nombre>.py` con clase que herede de `Supplier`
   (ver `base.py`). Implementar `name`, `required_creds`, `fetch_products(creds)`,
   `transform_item(raw, config)`.
2. Registrar en `scripts/suppliers/__init__.py` `_REGISTRY`.
3. Sumar `required_creds` a `system_audit.SUPPLIER_REQUIRED_KEYS` para que
   el audit semanal detecte keys faltantes.
4. Test en `tests/test_suppliers.py`.

### Cómo agregar un tenant nuevo

1. `tenants/<slug>/` con `config/{branding.json, config.json, clients.yml}`,
   `index.html`, `style.css`, `js/`, `netlify.toml`. Usar tenants existentes
   como template.
2. Entrada en `tenants/_registry.yml` con slug, state (testing primero),
   netlify_site_id, supplier.
3. Si el supplier necesita creds propias, `tenants/<slug>/.env`. Si no,
   usa el `.env` raiz.
4. Site Netlify: crear con `stop_builds=true` y `dir=tenants/<slug>`.

## Canal Telegram separado (tecnico vs comercial)

Variable de entorno `TELEGRAM_TECH_CHAT_ID`: si esta seteada, **redirige
todas las alertas tecnicas (healthcheck, post_deploy_check, system_audit)
a ese chat** y las saca del chat de los admins de `clients.yml`. Los
reportes comerciales nocturnos siguen yendo a admin+client del yaml.

Why: cuando se sumen clientes pagos, no comparten chat con el dev/ops.
Si no seteas la var, comportamiento legacy se mantiene (alerts → admins
del yaml).

Rate-limit: `alert_throttle.should_send()` deduplica alertas con el
mismo fingerprint en ventana de 30min (configurable via
`ALERT_THROTTLE_MIN`). Healthcheck corre cada 15min y antes podia
spamear el mismo problema; ahora lo silencia.

## Convenciones del repo

- **Timezone**: AR (`America/Argentina/Buenos_Aires`) en todos los timestamps
  que vayan a logs/Telegram/heartbeat. `run_daily.sh` y `run_frequent.sh`
  exportan `TZ`. Python hereda.
- **Commit marker**: `[run:YY-MM-DD]` en commits automaticos (dedup multi-nodo).
- **Cadena LLM**: Gemini → Cerebras → SambaNova → plantilla. Nunca quedarse
  sin mensaje de Telegram.
- **Tono Telegram**: "vendedor amigo" coloquial argentino. Sin palabras
  alarmistas en reportes comerciales.

## Backlog de mejoras

`MASTER_PROMPT.md` en la raíz tiene el backlog priorizado. Cuando el user diga *"continuá con el master prompt"* (o equivalente), abrirlo y tomar el primer item `status: pending` no bloqueado. Cada item lleva scope + acceptance. Cuando se cierra: status → completed, fecha, sha del commit.

## Antes de mergear

1. `pytest tests/` verde.
2. `./scripts/post_deploy_check.py` verde (si tocaste pipeline de data).
3. Cambios estructurales sobre prod (build_settings Netlify, crontab Pi,
   `.env` de prod) → avisar al user ANTES, mostrar diff/comando, esperar OK.
4. Push y validar en la Pi (`ssh jorge@100.112.235.98`).

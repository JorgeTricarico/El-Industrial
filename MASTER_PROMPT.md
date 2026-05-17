# Master Prompt — Backlog de mejoras

> **Cómo usar este archivo**
>
> Cuando el usuario diga *"continuá con el master prompt"* (o equivalente):
> 1. Leé este archivo de arriba abajo.
> 2. Tomá el **primer** item con `status: pending` que NO esté `blocked`.
> 3. Marcá `status: in_progress` en este archivo (sin commit todavía).
> 4. Implementá usando el **scope** + **acceptance** del item.
> 5. Cuando termines: `pytest tests/` verde + `./scripts/post_deploy_check.py` verde (si tocaste pipeline) + commit + push + validar en la Pi (`ssh jorge@100.112.235.98`).
> 6. Editá el item: `status: completed`, agregale `done: <YYYY-MM-DD>` y `commit: <sha>`.
> 7. Pasá al siguiente sin esperar permiso. Si el siguiente toma >2h, ofrecé partir.
> 8. Al final mandá un Telegram de cierre con el resumen (mismo patrón que sesiones anteriores).
>
> **Reglas heredadas de `CLAUDE.md` que aplican a TODO ítem:**
> - No efectos externos por default (`tests/conftest.py` mockea `send_alert`/`STATUS_DIR`).
> - Validar desde POV del cliente final (E2E browser > unitarios).
> - Cambios estructurales sobre prod (`.env`, crontab Pi, `build_settings` Netlify): **avisar antes**, mostrar el diff/comando, esperar OK.

---

## Backlog (ordenado por prioridad)

### M1 — Migrar `validate_prices` y `analyze_prices` a tenant-aware

- **status**: completed
- **done**: 2026-05-17
- **commit**: 73cec3f
- **prioridad**: ALTA (cierra deuda transitoria de Fase 2B)
- **estimado**: 1-2h
- **bloquea**: M2

**Problema**: `scripts/validate_prices.py` y `scripts/analyze_prices.py` leen el `.gz` desde `data/` del root. Por eso `update_products.process_tenant()` para `el-industrial` todavía espeja a `data/` raíz + `status/daily_accum.json` raíz (ver `PRIMARY_TENANT_SLUG` y `write_root_compat` en `scripts/update_products.py`). Esa rama existe SOLO para no romper estos dos scripts.

**Scope**:
- `validate_prices.py`: aceptar `--tenant <slug>` (default: primer tenant `active`). Leer de `tenants/<slug>/data/`.
- `analyze_prices.py`: idem (y mover los paths hardcoded `OLD_FILE`/`NEW_FILE` a args o auto-detección del último/anteúltimo `.gz` del tenant).
- Una vez que ambos consumers están migrados, borrar de `scripts/update_products.py`:
  - constante `PRIMARY_TENANT_SLUG`
  - funciones `write_root_compat` y mirror al root `STATUS_DIR`
- Borrar de `scripts/nightly_report.py`:
  - bloque "compat" del fallback al root accum
  - archivado del root accum en `main()`

**Acceptance**:
- `pytest tests/` verde con cobertura del flag `--tenant` en ambos scripts.
- `update_products.py` corre en la Pi y NO escribe a `data/` ni `status/daily_accum.json` del root (verificar con `ls -la` post-corrida).
- `nightly_report.py` corre y reporta el item de prueba normal (sin sanity-trip).
- `verify.sh --fast` verde.
- CLAUDE.md actualizado: mencionar que el pipeline ya no toca root, todo es per-tenant.

---

### M2 — `PRIMARY_TENANT_SLUG` configurable desde `_registry.yml`

- **status**: completed
- **done**: 2026-05-17
- **commit**: 73cec3f (cerrado por M1)
- **nota**: M1 borró TODA la lógica primary-aware del código (no quedó
  `PRIMARY_TENANT_SLUG` en ningún script ni heartbeat ni accum). Solo queda
  una mención narrativa en comentario de `post_deploy_check.py`
  (post-mortem). No hay nada que parametrizar. Si en el futuro vuelve a
  haber lógica primary-aware (ej. para un cliente "vidriera" del SaaS),
  reabrir este item.

**Problema (histórico)**: Una vez que M1 está hecho, `PRIMARY_TENANT_SLUG = "el-industrial"` hardcodeado en update_products y nightly_report ya no se usa para compat. Pero antes de onboardear un cliente real que NO sea el-industrial, queremos que la elección del primario sea dato, no código. Solo aplica si M1 dejó algo de lógica primary-aware (ej. heartbeat global).

**Scope**:
- En `tenants/_registry.yml`, sumar campo opcional `primary: true` a una entrada (default: la primera `active` encontrada si nadie tiene flag).
- Helper `tenants_registry.primary_slug()` o similar.
- Reemplazar usos hardcoded.

**Acceptance**:
- Cambiar el `primary` en `_registry.yml` y todos los scripts respetan el cambio sin rebuild.
- Tests cubren los 3 escenarios: nadie marcado, uno marcado, varios marcados (ganaría el primero).

---

### M3 — GC de `status/metrics.jsonl` y `reports/cron_log.txt`

- **status**: completed
- **done**: 2026-05-17
- **commit**: (siguiente)
- **prioridad**: ALTA (riesgo silencioso: disco Pi se llena en meses)
- **estimado**: 1h

**Problema**: `status/metrics.jsonl` se appendea cada corrida del cron (cada 30min). `reports/cron_log.txt` igual. Sin rotación, en 12 meses son varios cientos de MB en una Pi con SD card. `nightly_report` ya hace `prune_old_archives` para `status/archive/` pero estos dos archivos no se tocan.

**Scope**:
- En `scripts/refresh_heartbeat.py` o como step del cron diario: rotar `metrics.jsonl` si pasa de N MB (default 50MB), mover a `status/archive/metrics_<YYYY-MM>.jsonl.gz`. Idem `cron_log.txt`.
- `system_audit` chequea tamaño de estos archivos y alerta si crecen > umbral.
- O alternativa: log-rotate via cron del sistema (más simple pero requiere cambio en infra Pi → avisar antes).

**Acceptance**:
- Tests cubren la rotación (archivo con tamaño X → se rota, archivo chico no).
- En la Pi después de validar: `ls -lh status/metrics.jsonl` < umbral.
- `system_audit` sumado el check de tamaño.

---

### M4 — Heartbeat por nodo (no global)

- **status**: pending
- **prioridad**: MEDIA
- **estimado**: 1h

**Problema**: `status/heartbeat.json` hoy guarda solo el último nodo que corrió (`{"node": "raspberrypi", "last_run": "..."}`). Cuando arranca Mint también lo pisa. `system_audit.check_node_heartbeats()` solo ve uno. Necesitamos saber si la Pi lleva 8 días offline aunque Mint siga reportando.

**Scope**:
- Cambiar formato a `{"raspberrypi": {"last_run": ..., "version": ...}, "DESKTOP-MI43BOU": {...}}`.
- `update_products.update_heartbeat()`: mergea en el dict, no sobrescribe.
- `healthcheck.detect_*` y `system_audit.check_node_heartbeats` iteran las entradas.
- `dead_man_switch` también se adapta.

**Acceptance**:
- Tests cubren: nodo nuevo, nodo viejo, conflicto, formato legacy.
- En la Pi: heartbeat tiene 2+ entries después de que ambos nodos corran.
- system_audit alerta solo del nodo realmente caído.

---

### M5 — E2E que valida entrega de Telegram (no solo HTTP del sitio)

- **status**: pending
- **prioridad**: ALTA (sería el detector del próximo bug "19 días" para Telegram)
- **estimado**: 1-2h

**Problema**: `e2e_post_deploy.yml` valida que `el-industrial.netlify.app` responde HTTP 200 con data fresca. Pero ningún check valida que el reporte Telegram nocturno **realmente se entregó**. Si Telegram bloquea al bot, si el token se revoca, si los chat_ids cambian, lo descubrimos cuando el cliente nos pregunta.

**Scope**:
- `nightly_report` ya escribe `heartbeat.last_telegram_iso` cuando manda. Sumar workflow `.github/workflows/telegram_delivery_check.yml` (corre 1x/día 11:00 AR), que lee `status/heartbeat.json` del repo y verifica que `last_telegram_iso` está dentro de las últimas 26h. Si no: Telegram al admin.
- O variante: `healthcheck` ya tiene `dead_man_switch` lógica. Asegurar que dispara también cuando hay TELEGRAM token pero ningún envío exitoso.

**Acceptance**:
- Workflow nuevo (o extensión del existente) verde en CI.
- Test que simule `last_telegram_iso` viejo → debe disparar alerta.
- Documentar en CLAUDE.md.

---

### M6 — Crontab Pi al repo (`infra/crontab.example`)

- **status**: pending
- **prioridad**: BAJA
- **estimado**: 20min

**Problema**: La config del cron de la Pi vive solo en `crontab -e` de la Pi. Si se rompe la SD card, no hay reproducibilidad. `system_audit` tampoco puede chequear que el cron espera lo que el código espera.

**Scope**:
- `infra/crontab.example` con los entries actuales (extraer con `ssh jorge@100.112.235.98 crontab -l`, redactar paths sensibles si los hay).
- README breve en `infra/` con "cómo aplicar este crontab en una Pi nueva".
- `system_audit`: chequea (en la Pi) que el `crontab -l` actual matchea el del repo (diff). Solo cuando el script corre EN la Pi, no en GH Actions.

**Acceptance**:
- `infra/crontab.example` existe y es leíble.
- README explicito.
- `system_audit` corriendo en la Pi alerta si el crontab cambió fuera del repo.

---

### M7 — Cleanup de residuos del repo

- **status**: pending
- **prioridad**: BAJA (cosmético, pero ayuda al onboarding)
- **estimado**: 30min

**Problema**: La raíz del repo tiene residuos de experimentos viejos:
- `*.zip` (final_perfect.zip, final_sync.zip, layout_fix.zip, manual_fix.zip, new_layout.zip, perfect_final.zip, performance_fix.zip, premium_layout.zip, theme_size_fix.zip, ux_fix_tooltip.zip, ux_micro_fixes.zip) — 11 archivos zip
- `screenshot_audit.js`
- `script.js.old`
- `playwright.config.cjs` Y `playwright.config.js` (decidir cuál se queda)
- `scripts/test_ai_direct.py`, `scripts/test_endpoints.py`, `scripts/inspect_api_fields.py`, `scripts/list_models.py` — scripts de debug ad-hoc que parecen residuales
- `audit_final.png`, `t.png`, `wpp.png` en la raíz (si son del onboarding del cliente, mover a `docs/`)

**Scope**:
- `git rm` los archivos claramente residuales (zips, .old, screenshot_audit.js).
- Decidir qué playwright.config.* se queda y borrar el otro.
- Mover scripts de debug a `scripts/debug/` con un README breve ("estos son ad-hoc para investigar APIs, no corren en cron").
- `.gitignore` los .zip por las dudas.

**Acceptance**:
- `git status` clean, `ls` de la raíz solo muestra cosas que tienen sentido.
- `pytest tests/` y `verify.sh --fast` siguen verde.

---

### M8 — Sanity check de cantidad mínima por tenant

- **status**: pending
- **prioridad**: MEDIA
- **estimado**: 30min

**Problema**: `fetch_with_retries` ya rechaza respuestas con `len(data) <= 100`. Pero ese umbral es global y arbitrario. Si Bertual un día devuelve solo 80 items por bug, nadie se entera (rejection silenciosa). Y si otro tenant tiene un catálogo chico legítimo (200 items), 100 es bajo.

**Scope**:
- Per-tenant `min_products` en `tenants/<slug>/config/config.json` (default: 80% del último `.gz` exitoso).
- Cuando se rechaza por count, alerta admin (no cliente).
- Métrica en `metrics.jsonl`.

**Acceptance**:
- Tests cubren: count bajo umbral → rechazo + alerta; count OK → procesa normal.
- Demo de la alerta cuando se fuerza el corte.

---

### M9 — `system_audit` chequea status per-tenant

- **status**: pending
- **prioridad**: BAJA
- **estimado**: 30min

**Problema**: `system_audit.check_tenants_deploys` mira el `.gz` del tenant. Pero no chequea:
- Existencia/frescura de `tenants/<slug>/status/daily_accum.json` (¿update_products escribió hoy?)
- Tamaño del `tenants/<slug>/status/archive/` (¿algo no se está prunear?)
- `tenants/<slug>/.env` exists pero hace meses que no se reactualiza (clave a expirar)

**Scope**:
- Sumar checks a `system_audit.check_tenants_deploys` o función nueva.
- Tests.

**Acceptance**:
- 3 checks nuevos en el reporte semanal con sus tests.

---

### M10 — Implementar `HaedoSupplier` real

- **status**: blocked
- **bloqueo**: no hay cliente real del rubro eléctrico todavía
- **prioridad**: cuando se cierre 2do cliente

**Scope (preliminar)**:
- Identificar API/scraping de Electrónica Haedo.
- Implementar `scripts/suppliers/haedo.py` con la interface `Supplier`.
- Tests + integración en `system_audit.SUPPLIER_REQUIRED_KEYS`.

---

### M11 — Tests de carga / N tenants

- **status**: blocked
- **bloqueo**: necesitamos ≥3 tenants `active` para que tenga sentido
- **prioridad**: cuando llegue M10 cerrado y haya 3+ clientes

**Scope (preliminar)**:
- Simular run completo (`update_products → sync_tenants → nightly_report`) con N tenants mockeados.
- Medir que termina en <5min para no solapar con próximo cron.
- Detectar bottlenecks (HTTP secuencial vs paralelo).

---

## Items completados

(Vacío al 2026-05-17. Cuando vayas cerrando, moverlos acá con su SHA y fecha.)

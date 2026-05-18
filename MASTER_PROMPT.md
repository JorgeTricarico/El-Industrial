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
- **commit**: b863ace
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

- **status**: completed
- **done**: 2026-05-17
- **commit**: a2b3bed
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

- **status**: parcial
- **done**: 2026-05-17 (script ad-hoc), pendiente workflow GH Actions
- **prioridad**: ALTA

**Hecho (2026-05-17)**:
- `scripts/e2e_telegram_simulate.py`: inyecta accum sintetico con producto real (canary), corre `nightly_report.process_tenant_report`, valida sent=True + heartbeat avanzo + `.gz` intacto (sha256) + no rastros. Usado a mano en la Pi.

**Pendiente**:
- Workflow `.github/workflows/telegram_delivery_check.yml` (1x/día 11:00 AR) que lea `status/heartbeat.json` y alerte si `tenants.<slug>.last_telegram_iso` esta vencido (> 26h).
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

---

## Pendientes detectados en revisión 2026-05-18

### P1 — Skip items=0 + dead-man semanal

- **status**: pending
- **prioridad**: ALTA (ruido diario al cliente)
- **estimado**: 15min

**Problema**: Días sin cambios reales igual mandan "Sin novedades hoy. No se detectaron cambios..." El cliente B2B no necesita un mensaje cada día — quiere saber cuando algo se mueve. Pero tampoco queremos perder el dead-man-switch (si pasan 7 días sin mensaje, algo está roto).

**Scope**:
- Si `len(updated_items) == 0` AND el heartbeat tiene `tenants.<slug>.last_telegram_iso` en los últimos 7 días → no enviar. Log `nightly_quiet_skip`.
- Si pasan ≥ 7 días sin envío → mandar mensaje "Sistema OK, sin novedades esta semana" (dead-man visible).

**Acceptance**:
- Test: 6 días seguidos con items=0 → 0 mensajes. Día 7 → 1 mensaje semanal.
- Test: items=0 con `last_telegram_iso` de hace 1 día → no envía.

---

### P2 — Pre-write heartbeat antes del send (race inter-nodo)

- **status**: pending
- **prioridad**: MEDIA (raro pero posible)
- **estimado**: 15min

**Problema**: Si Pi y Mint corren `nightly_report` en la misma ventana, ambos leen heartbeat antes del push del otro → ambos envían. El dedupe per-día funciona intra-nodo, no entre nodos en la misma ventana de ~20s (tiempo del LLM).

**Scope**:
- En `process_tenant_report`, escribir `heartbeat.tenants.<slug>.last_telegram_iso` **antes** del `send_telegram()` (optimistic lock). Si `send_telegram` falla, dejarlo así igual: el `healthcheck.dead_man_switch` verifica que efectivamente llegue.
- Documentar el trade-off: preferimos perder 1 envío fallido a tener envíos duplicados.

**Acceptance**:
- Tests: si `send_telegram` retorna False, heartbeat queda actualizado (acepta el trade-off).
- Tests: 2 procesos simulados que pullean al mismo tiempo → solo 1 envía (segundo ve heartbeat actualizado).

---

### P3 — `_archive_accum` fail-safe

- **status**: pending
- **prioridad**: MEDIA
- **estimado**: 5min

**Problema**: Si `os.rename` falla (disco lleno, permisos, etc.), la excepción no está atrapada en el flujo principal. El accum no se archiva, mañana procesa los mismos cambios + los nuevos → mensaje inflado.

**Scope**:
- Wrap el `os.rename` con try/except, log `archive_fail` (ya existe), no propagar.
- Alternativa: si el rename falla, intentar copy + delete.

**Acceptance**:
- Test: monkeypatch `os.rename` para que lance OSError → `process_tenant_report` retorna OK igual, accum sigue ahí pero el envío fue exitoso.

---

### P5 — Hora real del proveedor en el header

- **status**: pending
- **prioridad**: BAJA
- **estimado**: 30min

**Problema**: Header dice "Lista del día — 18/05/2026 22:00". El lector no sabe si esa data es de cuando Bertual la generó (puede ser 6 horas atrás) o cuando nosotros la procesamos.

**Scope**: Si el supplier expone fecha de actualización, propagarla a un campo `supplier_updated_at` en el accum, y mostrarla en el header: "Lista del día — Bertual actualizó a las 18:42".

---

### P6 — Sacar `nightly_report` del cron de las 22:00

- **status**: blocked (requiere OK del user para tocar crontab Pi)
- **prioridad**: BAJA
- **estimado**: 5min

**Problema**: Cron corre `run_daily.sh` a las 20:00 y a las 22:00. Las 22:00 sirve como retry/refresh de `update_products` pero también dispara `nightly_report` que ya hizo dedup → quema ~20s de LLM al pedo.

**Scope**: Variable de entorno o flag en `run_daily.sh` para que la 2da corrida del día solo haga `update_products`, no `nightly_report`. O 2 scripts separados.

---

### P7 — Robustez del `git add` whitelist

- **status**: pending
- **prioridad**: BAJA
- **estimado**: 10min

**Problema**: El whitelist en `run_daily.sh` (post-incidente del .env.backup) cubre los paths actuales. Si un script nuevo escribe algo legitimo fuera del whitelist, no se commitea. Sin alerta.

**Scope**: Tras el `git add` y antes del `git commit`, log las paths staged. Si `git status` muestra archivos modificados que NO están en el staging area, log warning.

---

### P8 — Cleanup de residuos del repo (M7 expandido)

- **status**: pending
- **prioridad**: BAJA

**Items**:
- `*.zip` en la raíz (11 archivos).
- `script.js.old`, `screenshot_audit.js`.
- `playwright.config.cjs` vs `playwright.config.js` (decidir cuál).
- `scripts/test_ai_direct.py`, `scripts/test_endpoints.py`, `scripts/inspect_api_fields.py`, `scripts/list_models.py` → `scripts/debug/`.
- `audit_final.png`, `t.png`, `wpp.png` → `docs/` o borrar.
- `tests/e2e/frontend_audit.html` + `tests/e2e/screenshots/` → revisar si son útiles.

---

### P9 — Tests de border en `classify_magnitude`

- **status**: pending
- **prioridad**: BAJA
- **estimado**: 10min

**Scope**: Tests con avg=0.99% (debería ser negligible) vs 1.00% (debería ser minor); avg=2.99% vs 3.00% (minor → moderate). Mismo con max_pct.

---

### P10 — `e2e_telegram_simulate` con --tenant configurable mejor

- **status**: pending
- **prioridad**: BAJA

**Scope**: Default no hardcoded a `demo-electricidad`: tomar el primer tenant con `state=testing` del registry, o fallback al primero. Si no hay testing, requerir flag explícito.

---

### P11 — Reportar `.env.backup-...` a GitHub Support

- **status**: blocked (sólo lo puede hacer el user; gh cli no tiene endpoint)
- **prioridad**: ALTA (commit dangling con credenciales sigue accesible por SHA)

**Acción**: Form en https://support.github.com/contact/private-information con SHA `9f494ab` y file `.env.backup-20260517_112159` → GC en horas. El usuario decidió no rotar credenciales (free tier), así que esto reduce el blast radius.

---

### P12 — `STATUS_DIR` parametrizable en log_metric

- **status**: pending
- **prioridad**: BAJA
- **estimado**: 15min

**Problema**: `log_metric` en `nightly_report` usa `STATUS_DIR` global del módulo. Tests funcionan por monkeypatch via `conftest`. Pero si un consumer pasa `clients_path=<tenant>` y querría `log_metric` per-tenant, no puede.

---

## Items completados

(Vacío al 2026-05-17. Cuando vayas cerrando, moverlos acá con su SHA y fecha.)

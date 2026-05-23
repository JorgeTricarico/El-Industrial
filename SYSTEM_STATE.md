# SYSTEM_STATE.md — Estado vivo del sistema

> **Para agentes (Claude/otros):** este archivo es la foto del sistema en este
> momento. Antes de meter mano, leelo. Cuando termines de trabajar, actualizá
> las secciones tocadas en la misma PR. No es opcional: si el código cambió y
> esto quedó obsoleto, la próxima sesión vuelve a chocar contra el mismo
> diagnóstico que vos ya hiciste.
>
> **No mezclar con:**
> - `CLAUDE.md` → contrato inmutable (reglas, convenciones).
> - `MASTER_PROMPT.md` → backlog priorizado de mejoras.
> - `MEMORY.md` (auto-memory, fuera del repo) → contexto cross-sesión del agente.
>
> Acá va lo que cambia: nodos vivos, gaps abiertos, último deploy verificado.

---

## Última actualización

- **Fecha:** 2026-05-23
- **Agente:** Claude Sonnet 4.6 (sesión de Jorge)
- **Commit head al cierre:** ver git log
- **Tests:** 183 ✅
- **Producción:** verde — `el-industrial.netlify.app` sirviendo data del 22/05. Pi corrió 23/05 10:00 AR (2 nuevos productos).

---

## Nodos del cluster (estado real, no aspiracional)

| hostname           | rol                | online    | last_run AR        | last_telegram_iso       | notas |
|--------------------|--------------------|-----------|--------------------|-------------------------|-------|
| raspberrypi        | primary            | ✅ vía TS | 2026-05-21 08:31  | 2026-05-21 (real send)  | Cron `0 10,20,22 * * 1-6`. 10:00 agregado 2026-05-21. |
| DESKTOP-MI43BOU    | backup             | ⚠️ WSL    | 2026-05-18 21:00  | —                       | Cron `0 0 * * 1-6` UTC. Dispara solo si Windows despierto. |
| rv420              | backup             | ✅ vía TS | nunca             | —                       | `pending_onboard`. Sin repo clonado todavía. |
| linux-mint         | backup             | ❌        | hace 10+ días     | —                       | Offline en Tailscale. Cuando vuelva, bumpear a `active`. |
| github-actions     | cloud_last_resort  | ✅        | 2026-05-20 03:32  | 2026-05-20 (filler)     | Solo filler. Para actualizar real necesita Tailscale (ver gaps). |

**Cómo refrescar esta tabla:** `cat status/heartbeat.json | jq '.nodes'` + `tailscale status`.

---

## Salud del pipeline E2E (POV cliente)

Última verificación end-to-end producto → cliente: **2026-05-20 13:00 AR**.

- `tenants/el-industrial/data/lista_precio_26-05-20_json_compres.gz` → existe local + en Pi + en repo.
- `el-industrial.netlify.app/latest-json-filename.txt` → `data/lista_precio_26-05-20_json_compres.gz` ✅
- `el-industrial.netlify.app/latest-json-filename.json` → match ✅
- Post-deploy check Pi: ✅ (después del fix de `state=testing`)
- Telegram al admin con update real: ✅ enviado vía force-send 13:05 AR

**Cómo re-verificar:** ver "Antes de mergear" en `CLAUDE.md` punto 2 — `./scripts/post_deploy_check.py`.

---

## Gaps conocidos y workarounds

### G1 — Cloud no puede actualizar precios sin Tailscale

- **Síntoma:** si Pi + WSL caen, cloud manda filler pero no precios reales.
- **Por qué:** Bertual firewallea IPs de GH Actions (probado 2026-05-20: timeout 90s × 4 retries × 2 calls = 18min sin SYN-ACK).
- **Workaround actual:** Plan B en `cloud_update_resort.yml` falla rápido (30s/0 retries). Filler queda como mensaje al cliente.
- **Cierre del gap:** configurar 3 secrets de GH (`TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET`, `PI_SSH_KEY`) → Plan A activa SSH-Pi vía Tailscale. Pasos en MASTER_PROMPT.md / pendiente del user.

### G2 — Gemini API key expirada/rotada con frecuencia

- **Síntoma:** `llm_failed gemini: 400 API key expired`.
- **Workaround actual:** cadena LLM cae a SambaNova OK.
- **Cierre del gap:** rotar key en Google AI Studio + propagar (hay script demostrado el 2026-05-20 que sincroniza .env locales + Pi + GH secrets sin exponer la key).

### G3 — Cron de WSL solo dispara con Windows despierto

- **Síntoma:** ventanas de cron 00:00 UTC no disparan si la PC está dormida.
- **Workaround actual:** Pi cubre como primary. WSL es backup ocasional.
- **Cierre del gap:** o tarea programada de Windows que despierte WSL, o aceptarlo y depender de Pi + cloud-resort.

### G5 — Inconsistencia crontab Pi vs nodes.yml (menor)

- **Síntoma:** la entrada de las 10:00 AR en la Pi tiene weekday `1-6` (Lun-Sab), pero las de 20:00 y 22:00 corren todos los días (sin restricción). `nodes.yml` declara `1-6` para todas.
- **Workaround actual:** ninguno; las corridas de Domingo a 20:00/22:00 hacen `dup_skip` o filler-quiet si no hay accum, sin daño real.
- **Cierre del gap:** `ssh jorge@raspberrypi 'crontab -e'` y alinear las 3 entradas a `1-6`. Trivial pero requiere acción manual del user.

### G4 — `demo-electricidad` con supplier stub

- **Síntoma:** `tenants/demo-electricidad` queda con data vieja por diseño (sin supplier real).
- **Workaround actual:** `state: testing` → `post_deploy_check` y `healthcheck.detect_public_site_stale` skipean freshness (fix 2026-05-23).
- **Cierre del gap:** cuando consigamos cliente real para Electrónica Haedo, implementar el supplier de verdad y bumpear a `active`.

---

## Cambios recientes del sistema (changelog corto)

> Solo cambios que afectan operación. Detalles en git log.

- **2026-05-23** `healthcheck.detect_public_site_stale`: skip tenants testing + edad medida desde `file_date+20h` en vez de medianoche. Elimina falso positivo diario donde el runner de GH Actions (2-3h tarde) reportaba "deploy no llegando" aunque el deploy era reciente.
- **2026-05-21** Pi crontab: agregado `0 10 * * 1-6` (10:00 AR Lun-Sab). Cierra gap G5: cliente abre temprano y ve precios del día. Antes solo corría 20:00 + 22:00 AR.
- **2026-05-20** `3f85890` Plan B (Bertual desde runner) fail-fast 30s — probado que no funciona, IPs GH bloqueadas.
- **2026-05-20** `3c526c4` Dedup nightly_report permite update real supersede filler. Fix post_deploy_check testing-tenant. Cloud-resort checkout depth=0.
- **2026-05-20** `5e4da6c` Workflow nuevo `cloud_update_resort.yml`. Bertual API timeout/retries configurables. Tests retries + dedup discriminado.
- **2026-05-20** `a744862` Dedup discriminado: solo "Actualizacion automatica" cuenta. run_daily.sh aborta si pull falla. Cerebras model `qwen-3-235b-a22b-instruct-2507`.

---

## Cosas a NO romper (heurísticas frágiles)

- El dedup `[run:YY-MM-DD]` ahora requiere subject `^Actualizacion automatica`. Si renombrás el commit message del cron, **todos los nodos van a actualizar duplicado**.
- `run_daily.sh` aborta con exit 2 en pull fail. Si volvés a continuar-con-código-stale, vuelve el bug del 19-may.
- `post_deploy_check.py` salta freshness check si `state == "testing"`. Si cambiás el state machine, revisar.
- `nightly_report.process_tenant_report` permite supersede solo si `last_telegram_provider.startswith("filler_")`. Si renombrás los providers filler (ej. `filler_supplier_down` → `no_supplier`), el supersede deja de disparar.

---

## Cómo actualizar este archivo (instructivo para futuros agentes)

Al cerrar una sesión:

1. Actualizá la línea **"Última actualización"** (fecha + commit head + tests).
2. Si tocaste algún nodo, refrescá la tabla **Nodos**.
3. Si abriste un gap nuevo, agregalo a **Gaps** con: síntoma, causa, workaround, plan de cierre.
4. Si cerraste un gap, **eliminalo** (no dejar histórico — el git log ya lo tiene).
5. Agregá 1 línea al **changelog corto** con el SHA + cambio en lenguaje operacional (no "fix(x)", sino "qué cambió de cara al sistema").
6. Si tu cambio introdujo una invariante frágil, sumala a **Cosas a NO romper**.

Reglas:
- Máximo ~200 líneas. Si crece, comprimí gaps cerrados y changelog viejo.
- No documentar lo que el código documenta solo (esto NO reemplaza CLAUDE.md ni a los docstrings).
- Si una línea no es accionable o verificable, no va.

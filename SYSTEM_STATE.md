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

- **Fecha:** 2026-07-01
- **Agente:** Claude Opus 4.8 (sesión de Jorge)
- **Commit head al cierre:** ver git log
- **Tests:** 229 ✅ (coverage 59%; update_products 85%, auto_fix 69%)
- **Producción:** verde — Pi corrió 01/07 10:00 AR (`outcome=updated`). Data del 01/07 en repo.

---

## Nodos del cluster (estado real, no aspiracional)

| hostname           | rol                | online    | last_run AR        | last_telegram_iso       | notas |
|--------------------|--------------------|-----------|--------------------|-------------------------|-------|
| raspberrypi        | primary            | ✅ vía TS | 2026-05-21 08:31  | 2026-05-21 (real send)  | Cron `0 10,20,22 * * 1-6`. 10:00 agregado 2026-05-21. |
| DESKTOP-MI43BOU    | backup             | ⚠️ WSL    | 2026-06-30 00:01  | —                       | Rol ahora resuelto desde `nodes.yml` (backup). **PENDIENTE user**: crontab local dice `0 0 * * 1-6` y el reloj de WSL pasó a AR → dispara a medianoche AR (antes de todo run de la Pi) → `supplier_down`. Fix: `crontab -e` → `0 21 * * 1-6`. |
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

- **2026-07-01** `scripts/auto_fix.py` (break-glass, P15): auto-fix multi-agente para outage de ≥3 días sin update. Cadena `agy`: diagnóstico → fix → verificación adversarial → gate pytest (wrapper) → push. Clon aislado (agente no pushea a prod), opt-in `AUTO_FIX_ENABLED` (default OFF), cooldown 24h. **NO activado aún** — requiere `AUTO_FIX_ENABLED=1` en `.env` de la Pi. Depende de P14 (tests robustos).
- **2026-07-01** `healthcheck.diagnose()` (P13 parcial): escala si las últimas 3 corridas fallaron con `supplier_down` o `api_fail`. Antes solo miraba `api_fail` → un outage sostenido de `supplier_down` era invisible hasta el stale-check de 26h. Un `supplier_down` aislado sigue sin alertar (lo cubre el filler).
- **2026-07-01** Crontab de DESKTOP-MI43BOU: `0 0` → `0 21 * * 1-6` (drift de TZ: se computó para UTC y el reloj de WSL pasó a AR). Ahora corre después del run de las 20:00 de la Pi → `dup_skip`, sin ruido nocturno.
- **2026-07-01** `run_daily.sh` + `node_pulse.effective_role`: el rol operativo ahora se resuelve desde `infra/nodes.yml` (via `--resolve-role`), con override env y fallback legacy. Antes cualquier host que no dijera "mint" se auto-elegía `primary` — DESKTOP-MI43BOU (backup) se creía primary y pegaba a Bertual de madrugada. `supplier_down` (exit 3) ahora loguea `AVISO` en vez de `CRITICO` (era ruido sobre condición esperada/manejada).
- **2026-07-01** `run_daily.sh:53` bug latente: el `git pull ... | tee` enmascaraba el exit de git (el `if` veía el exit de `tee`=0). Un pull fallido seguía con código stale en vez de abortar (exit 2) — la protección anti-data-vieja del bug 19-may estaba rota. Fix: redirigir al log sin pipe. Test `test_pull_fail_aborts_with_exit_2` vuelve a verde.
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
- `run_daily.sh` pull principal (línea ~53) NO debe volver a usar `| tee`: enmascara el exit de git y rompe el abort en pull-fail (vuelve el bug de data vieja). Si necesitás ver el output en vivo, agregá `set -o pipefail` con cuidado (hay otros pipes con `grep` que saldrían 1 sin match).
- Rol operativo del nodo lo decide `node_pulse.effective_role` desde `nodes.yml`. Si sumás un nodo y no lo registrás ahí, cae al fallback legacy (no-"mint" ⇒ primary) y podría pushear duplicado. Registralo en `nodes.yml`.
- `auto_fix.py` es un ARMA CARGADA (agente autónomo que pushea a prod si pytest pasa). Guardrails que NO se tocan: gate de pytest lo corre el WRAPPER (no el agente), clon con origin local (el agente no llega a GitHub), opt-in por `.env`, cooldown. Su seguridad depende de la fuerza de los tests (P14). No activar en la Pi hasta que la cobertura sea sólida.

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

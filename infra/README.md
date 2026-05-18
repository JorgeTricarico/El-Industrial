# infra/ — Cluster de nodos

Este directorio define cómo se compone el cluster de devices que corren el
pipeline de El-Industrial.

## Por qué un cluster

- **Bertual (proveedor mayorista) NO es accesible desde GitHub Actions**
  (probado 2026-05-18: timeout 30s). La única forma de actualizar precios
  es desde nodos en redes argentinas.
- Un solo nodo (la Pi) es punto único de falla. Necesitamos N+1 para que
  el cliente final reciba lista actualizada todos los días Lun-Sab.
- Cada nodo es **idéntico**: mismo repo, mismo venv, mismo script. La
  única diferencia es el cron y el hostname.

## Archivos

| Archivo | Qué es |
|---|---|
| `nodes.yml` | Registry de todos los devices del cluster. Fuente de verdad. |
| `README.md` | Este archivo. |

## Cómo sumar un nodo nuevo

En el device que querés sumar (debe poder llegar a la red AR para reachear
Bertual):

```bash
# 1. Clonar el repo
git clone https://github.com/JorgeTricarico/El-Industrial.git
cd El-Industrial

# 2. Bootstrap (idempotente — podés correrlo varias veces)
./scripts/setup_node.sh <role> <cron_offset_min>
#   role:              primary | backup | dev
#   cron_offset_min:   minutos despues de las 20:00 AR
#                      (ej 0=20:00, 30=20:30, 60=21:00, 90=21:30, 120=22:00)
#   IMPORTANTE: coordinar el offset para no solapar con nodos existentes.
```

El script hace:
1. Crea `venv/` y instala `requirements.txt`.
2. Genera `.env` plantilla en chmod 600 (completar a mano con creds del nodo primario).
3. Agrega entry al `crontab` del usuario con el offset elegido.
4. Hace un pulso inicial al `status/heartbeat.json`.

### Después del bootstrap (a mano)

1. **Completar `.env`** con las credenciales (BERTUAL_*, TELEGRAM_*, GEMINI_*, etc).
   Conseguirlas del `.env` del nodo primario o de la persona a cargo.
2. **Agregar entrada en `infra/nodes.yml`** con tu hostname, role, cron, location.
   Bumpear `state: active`.
3. **Commit + push** del cambio en `nodes.yml`.
4. **Probar a mano** una corrida: `./scripts/run_daily.sh`.
5. **`system_audit`** confirma el nodo en el cluster en su próxima corrida.

## Coordinación de horarios

Los nodos NO se pisan entre sí (hay dedup vía commit-marker + heartbeat).
Pero conviene escalonar para que el envío sea lo más rápido posible:

```
20:00  raspberrypi (primary, fetcha Bertual y manda Telegram)
20:30  linux-mint (backup, si la Pi falló cubre)
21:00  DESKTOP-MI43BOU (WSL, backup ocasional)
21:30  rv420 (note vieja, backup permanente)
22:00  raspberrypi (retry — cubre si el primer intento falló)
23:30 UTC  github-actions (último recurso, solo manda mensaje sin data nueva)
```

Cada nodo posterior chequea si los anteriores ya hicieron el trabajo:
- Si sí → pulsa `dup_skip` y sale.
- Si no → toma el trabajo, fetcha Bertual, pushea, manda Telegram.

## Estados posibles de un nodo

| `state` | Qué significa |
|---|---|
| `active` | Corriendo cron. `system_audit` alerta si > 36h sin pulso. |
| `paused_offline` | Conocido como fuera de red. `system_audit` no alerta. |
| `pending_onboard` | Bootstrap iniciado pero `.env` o cron incompleto. |
| `dev` | Solo para experimentación. No corre cron real. |
| `retired` | Ya no se usa. Mantener en yaml por traceability hasta que pase >30 días sin pulso. |

## Trazabilidad

`status/heartbeat.json` registra **cada corrida de cada nodo** aunque no
haya hecho trabajo útil. Outcomes posibles:

- `started` — arrancó la corrida
- `dup_skip` — otro nodo ya lo hizo hoy
- `supplier_fail` — Bertual no respondió
- `updated` — fetcheó, escribió `.gz`, mandó Telegram
- `nightly_fail` — `nightly_report` salió con error
- `bootstrap` — primera corrida via setup_node.sh

`system_audit` cruza `infra/nodes.yml` con `heartbeat.json` y alerta de:
- Nodos `active` declarados pero sin pulso (cron roto, .env mal).
- Nodos que pulsan pero no están declarados (sin onboardear).
- Nodos `active` con último pulso > 36h.

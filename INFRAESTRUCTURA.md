# Infraestructura y Despliegue Automatizado

Este documento detalla cómo está configurada la ejecución periódica y automática del sistema **El-Industrial**, específicamente la actualización de los precios, para evitar la pérdida de conocimiento técnico a futuro.

## Nodo Principal de Ejecución: Raspberry Pi

El script que se encarga de consultar la API (Bertual), transformar los precios y enviarlos a Telegram (además de pushear los cambios a GitHub) **no corre en una máquina local estándar**, sino de forma "Headless" en una **Raspberry Pi**.

### 🌐 Red Privada (Tailscale MagicDNS)
La Raspberry Pi está conectada a la red privada virtual (VPN) provista por **Tailscale**. 
Esto permite conectarse remotamente a ella por SSH en cualquier lugar del mundo sin configurar puertos en tu router.

- **IP de Tailscale de la Pi:** `100.112.235.98`
- **Usuario/Hostname:** `jorge@100.112.235.98` (El hostname exacto puede referirse usando MagicDNS de Tailscale si está habilitado en tu máquina local).

### ⚙️ Automatización (Cronjobs)
La automatización se rige por las tareas programadas (Crontab) del usuario `jorge` dentro de la Raspberry Pi:

- **Ruta del Proyecto en la Pi:** `/home/jorge/El-Industrial/`
- **Ejecución Diaria:** Existe una entrada en `crontab -l` que detona el script `/home/jorge/El-Industrial/scripts/run_daily.sh` todos los días a las **20:00**. Este script ahora incluye la generación de un **Reporte Ejecutivo Nocturno** vía IA (Gemini).
- **Ingesta de Alta Frecuencia:** Se ha implementado `scripts/run_frequent.sh` para ejecuciones silenciosas (telemetría) que pueden programarse cada 1-2 horas para capturar cambios sin saturar el historial de versiones ni los reportes de Telegram.
- **Logs del Cron:** Toda salida de errores o de éxito cuando el servidor levanta los precios se guarda en `/home/jorge/El-Industrial/reports/cron_log.txt` (diario) y `reports/cron_frequent_log.txt` (frecuente).

### 🐍 Entorno de Python
El entorno virtual está alojado en `/home/jorge/El-Industrial/venv`.
Es fundamental que cualquier librería nueva (como `python-dotenv`, `requests`, `xlsxwriter`) que uses en tu computadora local al probar funcionalidades nuevas en modo desarrollador sea luego instalada en ese `venv` de la Raspberry Pi.

> **Incidencia Resuelta (Abril 2026):**
> Hubo un fallo del bot cron porque el paquete `dotenv` no estaba instalado en el entorno remoto, causando un error `ModuleNotFoundError: No module named 'dotenv'`. Se ha configurado correctamente ejecutando `.../venv/bin/pip install python-dotenv` en la Raspberry Pi.

### 🔄 Repositorio GITHUB (Ramas)
El sistema en la Pi realiza las actualizaciones haciendo commit directamente al repositorio y "pusheando".
Para asegurar compatibilidad cruzada entre ramas (si la Pi usa `master` localmente y Github usa `main`), el script `run_daily.sh` emplea estratégicamente `git push origin HEAD:main` de tal forma que los updates automáticos siempre terminen publicándose en la rama correcta online.

---
_Cualquier modificación o archivo nuevo agregado localmente debe ser pusheado a GitHub y luego subido (pull) en la Raspberry Pi si afecta el entorno general de actualización automática._

# 📘 Proyecto: Automatización de Precios Industrial (Bertual API)

Este documento establece las normas fundamentales de arquitectura, seguridad y desarrollo para este proyecto.

## ⚖️ Reglas de Oro (Business Rules)

1.  **Protección de Margen (CRÍTICO):** NUNCA se debe usar el campo `precio_resale` (con descuento) para reportes públicos. Siempre usar el campo `precio` (PVP Final).
2.  **Origen de Datos:** Usar siempre el campo `Precio` bruto de la API de Bertual.
3.  **Mandato de TDD:** Toda nueva funcionalidad o corrección debe incluir pruebas en `tests/`.

## 📊 Estrategia de Ingesta y Notificación (Ingeniería de Datos)

1.  **Ingesta de Alta Frecuencia:** El script debe ejecutarse varias veces al día para capturar telemetría. Estas ejecuciones deben ser **silenciosas** (flag `--silent`), limitándose a registrar métricas y actualizar el estado de los datos.
2.  **Acumulación Diaria:** Los cambios detectados durante el día deben guardarse en un buffer (`status/daily_accum.json`) para ser procesados al final de la jornada.
3.  **Reporte Ejecutivo Nocturno:** Se enviará un único reporte diario a Telegram. Este reporte incluirá:
    *   **Resumen IA:** Análisis de tendencias de precios y productos clave del día.
    *   **Análisis de Ventana de Carga:** El LLM analizará `status/metrics.jsonl` para determinar a qué horas el proveedor (Electronic Haedo) realiza sus cargas, permitiendo optimizar los horarios de los crons.
    *   **Estado de Infraestructura:** Informe de disponibilidad de los nodos y latencia de la API.

## 🛡️ Protocolos de Seguridad y Despliegue

1.  **Validación Pre-Flight:** Todo cambio en `scripts/update_products.py` DEBE ser validado con `python3 -m py_compile` antes de ser subido.
2.  **Invariante de Producción:** El éxito de una tarea no se mide por el `git push`, sino por un `curl` exitoso a la URL de producción (`https://el-industrial.netlify.app`).
3.  **Robustez del Frontend:** El `script.js` debe mantener la lógica de detección de "Magic Numbers" para GZIP, permitiendo fallos elegantes si el archivo se sirve descomprimido.
4.  **Doble Remoto:** La Raspberry Pi debe mantener siempre dos remotos (`origin` y `personal`) para asegurar que el mirror de Netlify esté siempre sincronizado con el repositorio principal.
5.  **Prohibición de Ignorar Datos:** La carpeta `data/` y los archivos `latest-json-filename.*` NUNCA deben ser ignorados por el `.gitignore` en las ramas de producción, ya que son la base de datos del cliente.
6.  **Prueba E2E Obligatoria:** Toda modificación en la lógica de ingestión o notificación DEBE ser validada ejecutando el script `scripts/e2e_telegram_simulate.py`. Este script inyecta un cambio seguro de 1 centavo, prueba toda la cadena de notificación hasta Telegram, y limpia su rastro. Si este script falla, el cambio no está listo para producción.

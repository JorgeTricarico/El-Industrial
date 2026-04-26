#!/bin/bash
PROYECTO="/home/jorge/El-Industrial"
LOG="$PROYECTO/reports/cron_log.txt"
URL_PROD="https://el-industrial.netlify.app"
FECHA=$(date +%Y-%m-%d)

echo "[$(date)] --- ACTUALIZACIÓN PRODUCCIÓN (JORGE REPO) ---" >> $LOG
cd $PROYECTO

# 1. Validación de Sintaxis
python3 -m py_compile scripts/update_products.py || { $PROYECTO/scripts/notify.sh "🚨 FALLO: Error de sintaxis en el script."; exit 1; }

# 2. Generar Datos
./venv/bin/python scripts/update_products.py >> $LOG 2>&1 || { $PROYECTO/scripts/notify.sh "🚨 FALLO: Error en generación de datos."; exit 1; }

# 3. DESPLIEGUE A PRODUCCIÓN (Tu Repo - Rama master)
git add .
git commit -m "prod: update precios ${FECHA} [skip ci]" >> $LOG 2>&1
echo "Subiendo cambios a Producción (Jorge Master)..." >> $LOG
git push personal master:master --force >> $LOG 2>&1

# 4. SMOKE TEST (Validación Real)
echo "Esperando despliegue de Netlify..." >> $LOG
sleep 30 
STATUS_CODE=$(curl -o /dev/null -s -w "%{http_code}" "$URL_PROD/latest-json-filename.json")

if [ "$STATUS_CODE" -eq 200 ]; then
    echo "✅ PRODUCCIÓN VALIDADA EXITOSAMENTE." >> $LOG
    $PROYECTO/scripts/notify.sh "✅ El Industrial: Web de Producción actualizada y validada."
else
    echo "⚠️ ALERTA: Push realizado pero la web devolvió HTTP $STATUS_CODE." >> $LOG
    $PROYECTO/scripts/notify.sh "⚠️ El Industrial: Push exitoso pero error HTTP $STATUS_CODE en la web."
fi

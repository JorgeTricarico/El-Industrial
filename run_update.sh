#!/bin/bash
PROYECTO="/home/jorge/El-Industrial"
LOG="$PROYECTO/reports/cron_log.txt"
URL_QA="https://lista-precio-el-industrial-dev.netlify.app"
URL_PROD="https://lista-precio-el-industrial-prd.netlify.app"
FECHA=$(date +%Y-%m-%d)

echo "[$(date)] --- INICIO PIPELINE QA -> PROD (JORGE REPO) ---" >> $LOG
cd $PROYECTO

# 1. Validación de Sintaxis
python3 -m py_compile scripts/update_products.py || { $PROYECTO/scripts/notify.sh "🚨 QA FALLÓ: Error de sintaxis en el script."; exit 1; }

# 2. Sincronizar local con origin (Martin) por si hubo cambios de lógica allí
git pull origin main >> $LOG 2>&1

# 3. Generar Datos
./venv/bin/python scripts/update_products.py >> $LOG 2>&1 || { $PROYECTO/scripts/notify.sh "🚨 QA FALLÓ: Error en generación de datos."; exit 1; }

# 4. DESPLIEGUE A QA (Tu Repo - Rama dev)
git add .
git commit -m "qa: deploy ${FECHA}" >> $LOG 2>&1
echo "Subiendo a entorno de QA (rama dev)..." >> $LOG
git push personal master:dev --force >> $LOG 2>&1

# 5. SMOKE TEST EN QA
echo "Esperando despliegue de Netlify QA..." >> $LOG
sleep 30 
STATUS_QA=$(curl -o /dev/null -s -w "%{http_code}" "$URL_QA/latest-json-filename.json")

if [ "$STATUS_QA" -eq 200 ]; then
    echo "✅ QA EXITOSO. Procediendo a PRODUCCIÓN (Tu Repo - Rama master)..." >> $LOG
    
    # 6. DESPLIEGUE A PRODUCCIÓN (Tu Repo - Rama master)
    git push personal master:master --force >> $LOG 2>&1
    
    # OPCIONAL: Mantener el repo de Martin actualizado como backup
    git push origin master:main >> $LOG 2>&1

    if [ $? -eq 0 ]; then
        echo "🚀 PRODUCCIÓN ACTUALIZADA EXITOSAMENTE EN TU REPO." >> $LOG
        $PROYECTO/scripts/notify.sh "✅ El Industrial: Actualización completada en QA y PRODUCCIÓN (Repo Jorge)."
    else
        echo "⚠️ Error al sincronizar con repo de Martin, pero Prod en Jorge está OK." >> $LOG
    fi
else
    echo "🚨 FALLÓ SMOKE TEST EN QA (HTTP $STATUS_QA). ABORTANDO PRODUCCIÓN." >> $LOG
    $PROYECTO/scripts/notify.sh "🛑 BLOQUEADO: QA falló. La web de producción no ha sido tocada."
    exit 1
fi

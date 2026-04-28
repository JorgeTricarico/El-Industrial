#!/bin/bash
FECHA=$(date +%Y-%m-%d)
PROYECTO="/home/jorge/El-Industrial"
REPORTE_PATTERN="lista_ferreteria_${FECHA}_*.xlsx"
LOG="$PROYECTO/reports/cron_log.txt"

echo "[$(date)] --- Iniciando Proceso Principal (Raspberry Pi) ---" >> $LOG

# 1. Auto-actualizar c?digo
cd $PROYECTO
git pull origin main >> $LOG 2>&1

# 2. Verificar si ya existe el reporte
if ls $PROYECTO/reports/$REPORTE_PATTERN 1> /dev/null 2>&1; then
    echo "Reporte de hoy ya existe localmente. Saltando." >> $LOG
    exit 0
fi

# 3. Ejecutar actualizaci?n
./venv/bin/python scripts/update_products.py >> $LOG 2>&1

if [ $? -eq 0 ]; then
    echo "?xito. Sincronizando reporte con el nodo de respaldo (iQual-Mint)..." >> $LOG
    # Copiar el reporte generado al otro nodo para evitar que el otro se dispare
    scp $PROYECTO/reports/$REPORTE_PATTERN jorge@100.115.152.45:$PROYECTO/reports/ >> $LOG 2>&1
else
    echo "FALLO en Raspberry Pi. El nodo de respaldo deber?a intentar a las 20:30." >> $LOG
fi


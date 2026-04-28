#!/usr/bin/env python3
import os, json, requests, time
from datetime import datetime
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
STATUS_DIR = os.path.join(BASE_DIR, "status")
ENV_FILE = os.path.join(BASE_DIR, ".env")

load_dotenv(ENV_FILE)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def get_ai_analysis(accum_data, metrics_data):
    if not GEMINI_API_KEY:
        return "ERROR: GEMINI_API_KEY no configurada."
    
    # --- Lógica de Resumen y Agrupación para evitar exceder límites de tokens ---
    new_items = list(accum_data.get("new", {}).values())
    updated_items = list(accum_data.get("updated", {}).values())
    
    # Agrupar actualizaciones por marca para ser más concisos
    brand_groups = {}
    for item in updated_items:
        brand = item.get("brand", "Sin Marca")
        if brand not in brand_groups: brand_groups[brand] = 0
        brand_groups[brand] += 1
    
    # Seleccionar top 10 cambios más significativos (o los primeros si no hay criterio de importancia)
    sample_updates = updated_items[:15]
    sample_new = new_items[:10]

    summary_data = {
        "total_new": len(new_items),
        "total_updated": len(updated_items),
        "updates_by_brand": brand_groups,
        "sample_updates": sample_updates,
        "sample_new": sample_new,
        "has_more": len(updated_items) > 15 or len(new_items) > 10
    }

    model_name = "gemini-3.1-flash-lite-preview"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
    
    prompt = f"""
Eres un analista experto para el sistema automatizado de 'El-Industrial'. 
Analiza los siguientes datos del día y redacta un 'Reporte Ejecutivo Nocturno' breve y conciso para Telegram.

Resumen de Datos del Día:
- Nuevos Productos: {summary_data['total_new']}
- Productos Actualizados: {summary_data['total_updated']}
- Distribución por Marcas: {json.dumps(brand_groups, ensure_ascii=False)}

Muestra de Cambios (para análisis de tendencia):
{json.dumps(sample_updates, ensure_ascii=False)}

{ "Nota: Hay más cambios que no se incluyen en esta muestra." if summary_data['has_more'] else "" }

Datos de Métricas de Infraestructura:
{json.dumps(metrics_data, ensure_ascii=False)[:1500]}

Instrucciones:
1. 🤖 **Resumen IA**: Analiza tendencias (¿subieron todas las marcas? ¿hay un porcentaje común?) y destaca productos clave.
2. 🕒 **Ventana de Carga**: Identifica a qué horas hubo 'updates' > 0 y sugiere horario para el cron.
"""

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        response = requests.post(url, json=payload, timeout=30)
        res = response.json()
        if "candidates" in res:
            return res["candidates"][0]["content"]["parts"][0]["text"].strip()
        else:
            return f"Error en API Gemini: {json.dumps(res)}"
    except Exception as e:
        return f"Excepción en la IA: {str(e)}"

def analyze_infrastructure(metrics):
    total = len(metrics)
    if total == 0:
        return "No hay métricas registradas hoy."
    
    fails = sum(1 for m in metrics if m.get("api") == "api_fail")
    avg_lat = sum(m.get("duration", 0) for m in metrics) / total
    
    uptime = ((total - fails) / total) * 100
    
    return (f"📊 **Estado de Infraestructura**\n"
            f"- **Disponibilidad API:** {uptime:.1f}%\n"
            f"- **Latencia Promedio:** {avg_lat:.2f}s\n"
            f"- **Ejecuciones Hoy:** {total}\n"
            f"- **Fallos:** {fails}")

def cleanup_old_archives(archive_dir, days=30):
    """Elimina archivos de archivo con más de N días para ahorrar espacio en la SD."""
    try:
        now = time.time()
        for f in os.listdir(archive_dir):
            fpath = os.path.join(archive_dir, f)
            if os.stat(fpath).st_mtime < now - (days * 86400):
                if os.path.isfile(fpath):
                    os.remove(fpath)
                    print(f"Limpieza: eliminado archivo antiguo {f}")
    except Exception as e:
        print(f"Error en limpieza: {e}")

def rotate_logs(log_paths, max_lines=1000):
    """Recorta los archivos de log para que no crezcan infinitamente."""
    for path in log_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                if len(lines) > max_lines:
                    with open(path, "w", encoding="utf-8") as f:
                        f.writelines(lines[-max_lines:])
                    print(f"Rotación: log {os.path.basename(path)} recortado.")
            except Exception as e:
                print(f"Error rotando log {path}: {e}")

def main():
    accum_path = os.path.join(STATUS_DIR, "daily_accum.json")
    metrics_path = os.path.join(STATUS_DIR, "metrics.jsonl")
    reports_log = os.path.join(BASE_DIR, "reports", "cron_log.txt")
    frequent_log = os.path.join(BASE_DIR, "reports", "cron_frequent_log.txt")
    
    # Load accum data
    accum_data = {"new": {}, "updated": {}}
    if os.path.exists(accum_path):
        try:
            with open(accum_path, "r", encoding="utf-8") as f:
                accum_data = json.load(f)
        except Exception as e:
            print(f"Error cargando daily_accum: {e}")
    
    # Load metrics
    metrics_data = []
    if os.path.exists(metrics_path):
        try:
            with open(metrics_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        metrics_data.append(json.loads(line))
        except Exception as e:
            print(f"Error cargando metrics: {e}")
            
    # If no data at all, skip sending or send empty report
    if not accum_data.get("new") and not accum_data.get("updated") and not metrics_data:
        print("No hay datos para reportar. Saliendo.")
        return
        
    ai_report = get_ai_analysis(accum_data, metrics_data)
    infra_report = analyze_infrastructure(metrics_data)
    
    full_report = (
        f"🌙 **Reporte Ejecutivo Nocturno**\n"
        f"📅 {datetime.now().strftime('%d/%m/%Y')}\n\n"
        f"{ai_report}\n\n"
        f"{infra_report}"
    )
    
    # Send to Telegram
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url_tg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            requests.post(url_tg, data={"chat_id": TELEGRAM_CHAT_ID, "text": full_report, "parse_mode": "Markdown"})
            print("Reporte nocturno enviado a Telegram.")
        except Exception as e:
            print(f"Error enviando a Telegram: {e}")
    else:
        print("TELEGRAM_TOKEN o CHAT_ID no configurados. Reporte generado en consola:\n")
        print(full_report)
    
    # Clean up (archive or delete)
    archive_dir = os.path.join(STATUS_DIR, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if os.path.exists(accum_path):
        os.rename(accum_path, os.path.join(archive_dir, f"daily_accum_{today}.json"))
    if os.path.exists(metrics_path):
        os.rename(metrics_path, os.path.join(archive_dir, f"metrics_{today}.jsonl"))

    # Ejecutar Mantenimiento
    cleanup_old_archives(archive_dir, days=30)
    rotate_logs([reports_log, frequent_log])

if __name__ == "__main__":
    main()

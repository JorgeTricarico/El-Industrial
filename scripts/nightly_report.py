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
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def get_ai_analysis(prompt):
    # Prioridad 1: Gemini 3.1 Flash-Lite (Modelo Gratis y Potente Mayo 2026)
    if GEMINI_API_KEY:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite-preview:generateContent?key={GEMINI_API_KEY}"
        max_retries = 3
        for attempt in range(max_retries):
            try:
                res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=40)
                if res.status_code == 429: # Rate limit
                    wait = (attempt + 1) * 10
                    time.sleep(wait)
                    continue
                if res.ok: 
                    return res.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            except:
                if attempt == max_retries - 1: break
                time.sleep(2)

    # Respaldo: Cerebras Qwen 2.5 72B
    if CEREBRAS_API_KEY:
        try:
            url = "https://api.cerebras.ai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {CEREBRAS_API_KEY}", "Content-Type": "application/json"}
            payload = {"model": "qwen2.5-72b", "messages": [{"role": "user", "content": prompt}]}
            res = requests.post(url, json=payload, headers=headers, timeout=30)
            if res.ok: return res.json()["choices"][0]["message"]["content"].strip()
        except: pass

    return "Error al generar análisis (API saturada o sin cuota)."

def main():
    accum_path = os.path.join(STATUS_DIR, "daily_accum.json")
    if not os.path.exists(accum_path): return

    with open(accum_path, "r", encoding="utf-8") as f:
        accum_data = json.load(f)
    
    updated_items = list(accum_data.get("updated", {}).values())
    
    # --- PROCESAMIENTO ESTADÍSTICO ---
    stats = {"marcas": {}, "aumentos": []}
    for item in updated_items:
        brand = item.get("marca") or item.get("brand") or item.get("Familia") or "Otras"
        stats["marcas"][brand] = stats["marcas"].get(brand, 0) + 1
        
        # Intentar calcular % si hay precio viejo y nuevo
        try:
            old_p = float(item.get("old", 0))
            new_p = float(item.get("new", 0))
            if old_p > 0:
                diff = ((new_p - old_p) / old_p) * 100
                stats["aumentos"].append({"n": item.get("name", brand), "p": diff, "m": brand})
        except: pass

    # Top 3 marcas y Top 10 aumentos más fuertes
    top_brands = sorted(stats["marcas"].items(), key=lambda x: x[1], reverse=True)[:5]
    top_hikes = sorted(stats["aumentos"], key=lambda x: x["p"], reverse=True)[:15]

    if len(updated_items) == 0:
        analysis = "✅ <b>Sin Novedades:</b> No se detectaron actualizaciones de precios ni productos nuevos en el día de hoy."
    else:
        prompt = f"""
Actúa como analista de precios profesional para 'El Industrial'. 
Sé MUY CONCISO. Reporte estilo Telegram. 
USA SOLO LISTAS Y BOLD. No escribas introducciones ni dramatismos bélicos.

DATOS:
- Cambios totales: {len(updated_items)}
- Marcas con más cambios: {top_brands}
- Mayores aumentos detectados: {top_hikes}

INSTRUCCIONES:
1. ⚠️ **ADVERTENCIAS**: Menciona si una marca subió más de un 10% o si hubo cambios masivos de lista. Usa un tono sobrio.
2. 📈 **ESTADÍSTICAS**: Resumen rápido de aumentos promedios y marcas afectadas.
3. 🕒 **CARGA**: Indica la hora estimada de carga del proveedor basada en los datos.
4. **FORMATO**: Usa <b>texto</b> para resaltar. No uses Markdown (* o _).
"""
        analysis = get_ai_analysis(prompt)
        # Limpiar posibles caracteres Markdown que la IA meta por inercia
        analysis = analysis.replace("*", "").replace("_", "")
    
    now = datetime.now()
    fecha = now.strftime("%d/%m/%Y")
    hora = now.strftime("%H:%M")
    full_report = f"<b>🌙 REPORTE INDUSTRIAL - {fecha} {hora}</b>\n\n{analysis}"
    
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        url_tg = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        # Usamos HTML que es mucho más estable
        res = requests.post(url_tg, data={"chat_id": TELEGRAM_CHAT_ID, "text": full_report, "parse_mode": "HTML"})
        if not res.ok:
            # Fallback total a texto plano
            requests.post(url_tg, data={"chat_id": TELEGRAM_CHAT_ID, "text": full_report})

    # Archivar
    archive_dir = os.path.join(STATUS_DIR, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.rename(accum_path, os.path.join(archive_dir, f"accum_{ts}.json"))

if __name__ == "__main__":
    main()

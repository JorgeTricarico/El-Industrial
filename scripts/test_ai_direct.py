import os, json, requests
from dotenv import load_dotenv
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)
load_dotenv(os.path.join(BASE_DIR, '.env'), override=True)

GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

def get_ai_summary(changes):
    model_name = 'gemini-3.1-flash-lite'
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}'
    prompt = f'Eres un experto analista. Resume estos cambios de precios para el dueño en 3 puntitos breves: {json.dumps(changes)}. Sé breve.'
    payload = {'contents': [{'parts': [{'text': prompt}]}]}
    response = requests.post(url, json=payload)
    res = response.json()
    if 'candidates' in res:
        return res['candidates'][0]['content']['parts'][0]['text']
    else:
        return f'ERROR: {json.dumps(res)}'

sample_changes = {
    'updated': [{'code': 'TU01', 'detalle': 'Tubo 1/2', 'old': '100', 'new': '115'}, {'code': 'TO02', 'detalle': 'Tornillo 10mm', 'old': '20', 'new': '22'}],
    'new': [{'code': 'LA05', 'detalle': 'Llana acero', 'new': '450'}]
}

print('Llamando a Gemini 3.1 Flash Lite...')
summary = get_ai_summary(sample_changes)
print('Resumen:', summary)

cap = f'🚀 TEST IA (3.1 Flash Lite)\n\n{summary}'
url_tg = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
requests.post(url_tg, data={'chat_id': TELEGRAM_CHAT_ID, 'text': cap})
print('Mensaje enviado a Telegram.')

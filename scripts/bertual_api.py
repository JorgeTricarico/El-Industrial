import os
import json
import time
import urllib.request
import urllib.error
from dotenv import load_dotenv

# Automatically load local .env if external scripts bypass main runners
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_FILE)


# Timeout y reintentos configurables. Default 30s suficiente en LAN/Pi.
# Para GH Actions runners (latencia mas alta + posible firewall), exportar
# BERTUAL_TIMEOUT=90 y BERTUAL_RETRIES=3 al lanzar update_products.
DEFAULT_TIMEOUT = int(os.getenv("BERTUAL_TIMEOUT", "30"))
DEFAULT_RETRIES = int(os.getenv("BERTUAL_RETRIES", "1"))


class BertualAPIClient:
    def __init__(self, cuit=None, password=None, client_id=None, api_url=None,
                 timeout=None, retries=None):
        self.cuit = cuit or os.getenv("BERTUAL_CUIT")
        self.password = password or os.getenv("BERTUAL_PASSWORD")
        self.client_id = client_id or os.getenv("BERTUAL_CLIENT_ID")
        self.api_url = api_url or os.getenv("API_URL", "https://autogestion-ehaedo.bertual.com.ar:8200/api")
        self.token = None
        self.timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
        self.retries = retries if retries is not None else DEFAULT_RETRIES

        if not self.cuit or not self.password or not self.client_id:
            raise ValueError("Credentials for Bertual API (CUIT, PASSWORD, CLIENT_ID) are missing. Check your .env file.")

    def _urlopen_with_retry(self, req, label):
        """urlopen + retries con backoff exponencial para timeouts/transitorios."""
        last_err = None
        for attempt in range(self.retries + 1):
            try:
                return urllib.request.urlopen(req, timeout=self.timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                if attempt < self.retries:
                    wait = 2 ** attempt
                    time.sleep(wait)
                    continue
        raise RuntimeError(f"Bertual {label} fallo tras {self.retries + 1} intentos: {last_err}")

    def login(self):
        data = json.dumps({"cuit": self.cuit, "password": self.password}).encode("utf-8")
        req = urllib.request.Request(f"{self.api_url}/login", data=data, headers={"Content-Type": "application/json"})
        with self._urlopen_with_retry(req, "login") as response:
            res_data = json.loads(response.read().decode())
            self.token = res_data.get("token")
            if not self.token:
                raise PermissionError("Failed to obtain a valid token from Bertual API. Check credentials.")
            return self.token

    def _fetch_json(self, url, max_mb=10):
        if not self.token:
            self.login()
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        max_bytes = max_mb * 1024 * 1024

        with self._urlopen_with_retry(req, "fetch") as response:
            content_length = response.getheader("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise MemoryError(f"Payload size {content_length} bytes exceeds maximum allowed limit of {max_bytes} bytes.")
            
            raw_data = response.read(max_bytes + 1)
            if len(raw_data) > max_bytes:
                raise MemoryError(f"Payload size exceeds maximum allowed limit of {max_bytes} bytes.")
                
            return json.loads(raw_data.decode())

    def fetch_products(self):
        url = f"{self.api_url}/precios/?q=&f=&cl={self.client_id}"
        res_data = self._fetch_json(url)
        return res_data.get("data", [])

    def fetch_items(self):
        url = f"{self.api_url}/items/?q=&f=&cl={self.client_id}"
        return self._fetch_json(url)

    def fetch_familias(self):
        url = f"{self.api_url}/familias"
        return self._fetch_json(url)
        
    def fetch_saldos(self):
        url = f"{self.api_url}/saldos/?cl={self.client_id}"
        return self._fetch_json(url)
        
    def fetch_ctacte(self, from_date="2026-01-01"):
        url = f"{self.api_url}/ctacte/?cl={self.client_id}&f={from_date}"
        return self._fetch_json(url)

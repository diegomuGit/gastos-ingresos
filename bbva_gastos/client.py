import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import jwt
import requests

from bbva_gastos.normalize import consent_valid_until

API_ORIGIN = "https://api.enablebanking.com"
ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "secrets"
DATA = ROOT / "data"
SESSION_PATH = DATA / "session.json"


class ApiError(RuntimeError):
    def __init__(self, status, body):
        super().__init__(f"Enable Banking respondió {status}: {body}")
        self.status = status
        self.body = body


def load_key():
    files = sorted(SECRETS.glob("*.pem"))
    if not files:
        raise SystemExit(
            "No hay clave en secrets/. "
            "Descarga el .pem al registrar la app y déjalo en secrets/ sin renombrarlo."
        )
    if len(files) > 1:
        names = ", ".join(path.name for path in files)
        raise SystemExit(f"Hay varias claves en secrets/: {names}. Deja solo la de esta app.")
    return files[0].stem, files[0].read_bytes()


def auth_header(application_id, private_key):
    issued = int(datetime.now(timezone.utc).timestamp())
    token = jwt.encode(
        {
            "iss": "enablebanking.com",
            "aud": "api.enablebanking.com",
            "iat": issued,
            "exp": issued + 3600,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": application_id},
    )
    return {"Authorization": f"Bearer {token}"}


def call(method, path, headers, **kwargs):
    response = requests.request(
        method,
        f"{API_ORIGIN}{path}",
        headers=headers,
        timeout=60,
        **kwargs,
    )
    if response.status_code >= 400:
        raise ApiError(response.status_code, response.text)
    if not response.content:
        return {}
    return response.json()


def application(headers):
    return call("GET", "/application", headers)


def aspsps(headers, country):
    payload = call("GET", "/aspsps", headers, params={"country": country})
    return payload.get("aspsps") or []


def start_authorization(headers, bank, redirect_url, psu_type, state):
    body = {
        "access": {"valid_until": consent_valid_until(int(bank["maximum_consent_validity"]))},
        "aspsp": {"name": bank["name"], "country": bank["country"]},
        "state": state,
        "redirect_url": redirect_url,
        "psu_type": psu_type,
        "language": "es",
    }
    return call("POST", "/auth", headers, json=body)


def create_session(headers, code):
    return call("POST", "/sessions", headers, json={"code": code})


def balances(headers, account_uid):
    return call("GET", f"/accounts/{account_uid}/balances", headers)


def transactions(headers, account_uid, date_from):
    params = {"date_from": date_from}
    collected = []
    while True:
        page = call("GET", f"/accounts/{account_uid}/transactions", headers, params=params)
        collected.extend(page.get("transactions") or [])
        token = page.get("continuation_key")
        if not token:
            return collected
        params["continuation_key"] = token


def save_session(session):
    DATA.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


def load_session():
    if not SESSION_PATH.exists():
        raise SystemExit("No hay sesión. Ejecuta primero: python -m bbva_gastos connect")
    return json.loads(SESSION_PATH.read_text(encoding="utf-8"))


def choose_redirect(app, preferred="http://127.0.0.1:8765/callback"):
    urls = app.get("redirect_urls") or []
    if preferred in urls:
        return preferred
    local = [url for url in urls if url.startswith("http://127.0.0.1:")]
    if len(local) == 1:
        return local[0]
    listed = ", ".join(urls) or "(ninguna)"
    raise SystemExit(
        "La app no tiene el redirect local esperado. "
        f"Añade exactamente {preferred} en Enable Banking. URLs actuales: {listed}"
    )


def callback_address(redirect_url):
    parsed = urlparse(redirect_url)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.port:
        raise SystemExit(f"El redirect tiene que ser local con puerto: {redirect_url}")
    return parsed.hostname, parsed.port, parsed.path or "/"

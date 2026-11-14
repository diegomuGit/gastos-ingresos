"""Importa la cartera desde Sheets con acceso de solo lectura."""

import json
import os
import re
import subprocess
import unicodedata
from datetime import datetime, timezone
from tempfile import NamedTemporaryFile
from threading import Lock

import requests

from bbva_gastos.client import SECRETS
from bbva_gastos.portfolio import PORTFOLIO, snapshot_path, validate_snapshot


SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"
CLIENT = SECRETS / "cartera-cliente.json"
TOKEN = SECRETS / "cartera-token.json"
SERVICE_ACCOUNT = SECRETS / "cartera-servicio.json"
TAB_NAMES = {"historico", "stocksyfondos", "posiciones", "stocks", "acciones", "fondos",
             "fondosref", "targets", "objetivos", "flujos", "crypto", "cripto", "cryptomovs", "criptomovs"}
AUTH_MESSAGE = "Autoriza Google Sheets con el comando «autorizar-cartera» indicado en el README."
SYNC_LOCK = Lock()


class SyncError(ValueError):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def _write_atomic(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _sheet_id(value):
    if not isinstance(value, str):
        raise ValueError("Indica el ID o la URL de la hoja al ejecutar «autorizar-cartera».")
    value = value.strip()
    url = re.fullmatch(r"https://docs\.google\.com/spreadsheets/d/([\w-]+)(?:[/?#].*)?", value, re.ASCII)
    identifier = url[1] if url else value
    if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        raise ValueError("El ID o la URL de Google Sheets no son válidos.")
    return identifier


def authorize(source=None):
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from oauthlib.oauth2 import OAuth2Error
    except ImportError as error:
        raise ValueError("Instala las dependencias de requirements.txt para conectar Google Sheets.") from error
    if not CLIENT.is_file():
        raise ValueError("Guarda un cliente OAuth de escritorio en secrets/cartera-cliente.json. Consulta el README.")
    if source is None and TOKEN.is_file():
        try:
            source = json.loads(TOKEN.read_text(encoding="utf-8")).get("spreadsheet_id")
        except (ValueError, AttributeError):
            raise ValueError("La configuración guardada no es válida. Pasa la URL de la hoja al autorizar.") from None
    identifier = _sheet_id(source)
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), scopes=[SCOPE])
        credentials = flow.run_local_server(port=0, timeout_seconds=180, prompt="consent",
                                            success_message="Autorización recibida. Puedes cerrar esta pestaña.")
    except (OAuth2Error, KeyError, AttributeError, ValueError, requests.RequestException) as error:
        raise ValueError("Google no completó la autorización de la cartera. Revisa el cliente y la cuenta.") from error
    if not credentials.refresh_token:
        raise ValueError("Google no devolvió un token de renovación para la cartera.")
    metadata = _read_google(identifier, credentials, values=False)
    if not metadata.get("sheets"):
        raise ValueError("La hoja autorizada no contiene pestañas.")
    config = json.loads(credentials.to_json())
    config["spreadsheet_id"] = identifier
    _write_atomic(TOKEN, json.dumps(config))


def _get(session, endpoint, params):
    response = session.get(endpoint, params=params, timeout=20, allow_redirects=False)
    if response.status_code in (401, 403, 404):
        raise SyncError("Google no permite leer la hoja. Revisa el acceso de la cuenta y la API de Google Sheets.", 502)
    if not response.ok or response.is_redirect:
        raise SyncError("Google Sheets no respondió correctamente. Vuelve a intentarlo.", 502)
    data = response.json()
    if not isinstance(data, dict):
        raise SyncError("Google Sheets devolvió una respuesta no válida.", 502)
    return data


def _read_google(identifier, credentials, *, values=True):
    from google.auth.exceptions import GoogleAuthError, RefreshError
    from google.auth.transport.requests import AuthorizedSession

    endpoint = f"{SHEETS_API}/{identifier}"
    try:
        with AuthorizedSession(credentials, refresh_timeout=15) as session:
            metadata = _get(session, endpoint, {"fields": "properties(timeZone),sheets(properties(title))"})
            if not values:
                return metadata
            titles = [sheet["properties"]["title"] for sheet in metadata["sheets"]]
            zone = metadata["properties"]["timeZone"]
            titles = [title for title in titles
                      if re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFD", title.lower())) in TAB_NAMES]
            if not isinstance(zone, str) or not zone:
                raise ValueError("La hoja no indica su zona horaria.")
            if not titles:
                raise SyncError("La hoja no contiene pestañas reconocidas de cartera.", 422)
            ranges = ["'" + title.replace("'", "''") + "'" for title in titles]
            response = _get(session, endpoint + "/values:batchGet",
                            {"ranges": ranges, "valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "SERIAL_NUMBER"})
            grids = response["valueRanges"]
            if len(grids) != len(titles):
                raise ValueError("Número de pestañas inesperado.")
            return {title: grid.get("values", []) for title, grid in zip(titles, grids)}, zone
    except RefreshError as error:
        if getattr(credentials, "service_account_email", None):
            raise SyncError("Google rechazó la clave de la cuenta de servicio. Revisa que siga activa.", 503) from error
        raise SyncError("La autorización de Google ha caducado. " + AUTH_MESSAGE, 503) from error
    except SyncError:
        raise
    except (requests.RequestException, GoogleAuthError, KeyError, TypeError, AttributeError) as error:
        raise SyncError("No se pudo leer Google Sheets. Vuelve a intentarlo.", 502) from error
    except ValueError as error:
        raise SyncError("Google Sheets devolvió una respuesta no válida.", 502) from error


def _load_google():
    try:
        from google.oauth2 import service_account
        from google.oauth2.credentials import Credentials
    except ImportError as error:
        raise SyncError("Instala las dependencias de requirements.txt para conectar Google Sheets.", 503) from error
    config_path = SERVICE_ACCOUNT if SERVICE_ACCOUNT.is_file() else TOKEN
    if not config_path.is_file():
        raise SyncError("Falta conectar tu cuenta de Google. " + AUTH_MESSAGE, 503)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        identifier = _sheet_id(config["spreadsheet_id"])
        if config_path == SERVICE_ACCOUNT:
            if config.get("type") != "service_account" or config.get("token_uri") != "https://oauth2.googleapis.com/token":
                raise ValueError("La clave debe corresponder a una cuenta de servicio de Google.")
            credentials = service_account.Credentials.from_service_account_info(config, scopes=[SCOPE])
        else:
            credentials = Credentials.from_authorized_user_info(config, scopes=[SCOPE])
    except (OSError, KeyError, TypeError, ValueError) as error:
        if config_path == SERVICE_ACCOUNT:
            raise SyncError("Revisa la clave y el ID de hoja en secrets/cartera-servicio.json.", 503) from error
        raise SyncError("No se puede leer la configuración local de Google. " + AUTH_MESSAGE, 503) from error
    return _read_google(identifier, credentials)


def synchronize():
    if not SYNC_LOCK.acquire(blocking=False):
        raise SyncError("Ya hay una sincronización de cartera en curso.", 409)
    try:
        tabs, zone = _load_google()
        try:
            result = subprocess.run(["node", str(PORTFOLIO / "sheets.js")],
                                    input=json.dumps({"tabs": tabs, "zone": zone}), encoding="utf-8",
                                    capture_output=True, timeout=20, check=False)
        except FileNotFoundError as error:
            raise SyncError("Instala Node.js para calcular la cartera.", 503) from error
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SyncError("No se pudo ejecutar el cálculo de la cartera.", 500) from error
        if result.returncode == 2:
            raise SyncError(result.stderr.strip(), 422)
        if result.returncode:
            raise SyncError("No se pudo calcular la cartera.", 500)
        try:
            portfolio = json.loads(result.stdout)
            validate_snapshot(portfolio)
            synced_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            portfolio["meta"]["sincronizadoEn"] = synced_at
            _write_atomic(snapshot_path(), json.dumps(portfolio, ensure_ascii=False, allow_nan=False))
        except (ValueError, OSError) as error:
            raise SyncError("No se pudo guardar una copia válida de la cartera.", 500) from error
        return {"sincronizado_en": synced_at}
    finally:
        SYNC_LOCK.release()

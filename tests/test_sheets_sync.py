import json
import shutil
import urllib.error

import pytest

from bbva_gastos import sheets_sync, wealth
from test_dashboard import api, conn, get, post
from test_transfers import _archive, _catalog


SYNC = "/api/patrimonio/sincronizar"


def test_sync_imports_serial_dates_and_preserves_copy_on_failure(conn, api, monkeypatch):
    if not shutil.which("node"):
        pytest.skip("La sincronización calcula la cartera con Node.")
    archive = _archive(conn, monkeypatch, _catalog())
    wealth.save_bank_close(conn, "2026-01-01", "400")
    wealth.save_bank_close(conn, "2026-01-02", "500")
    wealth.save_bank_close(conn, "2026-01-03", "550")
    tabs = {
        "Histórico": [["Fecha", "Total", "Aportacion"], [46023, 1000, 0], [46024, 1260, 250]],
        "Cripto": [["Fecha", "Valor_EUR", "Cantidad"], [46023, 100, 1], [46024, 110, 1]],
    }
    monkeypatch.setattr(sheets_sync, "_load_google", lambda: (tabs, "Etc/GMT"))

    assert get(api + "/api/patrimonio")["sincronizado_en"] is None
    result = post(api, SYNC, {})
    saved = archive.read_bytes()
    assert json.loads(saved)["meta"]["sincronizadoEn"] == result["sincronizado_en"]
    summary = get(api + "/api/patrimonio")
    assert summary["sincronizado_en"] == result["sincronizado_en"]
    assert summary["fecha"] == "2026-01-02"
    assert summary["ultimo"] == {"bbva": "2026-01-03", "myinvestor": "2026-01-02", "cripto": "2026-01-02"}
    assert get(api + "/api/patrimonio/evolucion")["desglose"]["myinvestor"] == "10.00"

    tabs["Cripto"][-1][1] = "#REF!"
    with pytest.raises(urllib.error.HTTPError) as invalid:
        post(api, SYNC, {})
    assert invalid.value.code == 422
    assert archive.read_bytes() == saved
    assert get(api + "/api/patrimonio")["sincronizado_en"] == result["sincronizado_en"]

    def disconnected():
        raise sheets_sync.SyncError("Google no está disponible.", 502)

    monkeypatch.setattr(sheets_sync, "_load_google", disconnected)
    with pytest.raises(urllib.error.HTTPError) as failed:
        post(api, SYNC, {})
    assert failed.value.code == 502
    assert archive.read_bytes() == saved


def test_sync_requires_local_json_and_rejects_overlapping_requests(api, monkeypatch):
    def no_access():
        pytest.fail("No debe consultar Google para esta petición.")

    monkeypatch.setattr(sheets_sync, "_load_google", no_access)
    with pytest.raises(urllib.error.HTTPError) as external:
        post(api, SYNC, {}, {"Origin": "https://example.com"})
    assert external.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as non_json:
        post(api, SYNC, {}, {"Content-Type": "text/plain"})
    assert non_json.value.code == 415
    with sheets_sync.SYNC_LOCK:
        with pytest.raises(urllib.error.HTTPError) as overlapping:
            post(api, SYNC, {})
    assert overlapping.value.code == 409

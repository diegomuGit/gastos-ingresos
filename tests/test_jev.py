import pytest

from bbva_gastos import cli, jev
from bbva_gastos.db import connect, save_labels, upsert_movements
from test_db import _db, _row, TMP


def test_refresh_bypasses_cached_decisions(monkeypatch):
    row = _row(concepto="Comercio sin regla")
    cached = {"categoria": "ocio", "origen": "jev", "confianza": 0.5, "revisar": False}
    fresh = {**cached, "categoria": "compras", "confianza": 0.9}
    monkeypatch.setattr(jev, "_load_cache", lambda: {jev.cache_key(row): cached})
    saved = []
    calls = []
    monkeypatch.setattr(jev, "_save_cache", lambda data: saved.append(data.copy()))

    def ask(key, movements):
        calls.append(movements)
        return [fresh], 10

    monkeypatch.setattr(jev, "_ask", ask)
    assert jev.classify([row], "test")[0][0]["categoria"] == "ocio"
    assert not calls
    assert jev.classify([row], "test", refresh=True)[0][0]["categoria"] == "compras"
    assert len(calls) == 1
    assert saved[0][jev.cache_key(row)] == fresh


def test_cli_refresh_failure_preserves_previous_classification(monkeypatch):
    conn = _db("refresh.sqlite")
    row = _row(concepto="Comercio sin regla")
    upsert_movements(conn, [row])
    save_labels(conn, [{**row, "categoria": "ocio", "origen": "jev", "confianza": 0.8, "revisar": False}])
    conn.close()
    monkeypatch.setenv("BBVA_GASTOS_DB", str(TMP / "refresh.sqlite"))
    monkeypatch.setattr(cli, "load_api_key", lambda key: "test-key")

    def fail(movements, key, *, refresh):
        assert refresh is True
        assert len(movements) == 1
        raise jev.JevError("Proveedor no disponible")

    monkeypatch.setattr(cli, "classify", fail)
    with pytest.raises(jev.JevError):
        cli.clasificar_movimientos(None, rehacer=True)
    conn = connect()
    try:
        stored = conn.execute("SELECT categoria, origen_clasificacion FROM movimientos").fetchone()
        assert tuple(stored) == ("ocio", "jev")
    finally:
        conn.close()

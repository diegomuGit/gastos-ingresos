import csv
import io
import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
import urllib.error
import urllib.request
from decimal import Decimal

import pytest
import requests

from bbva_gastos import db, lectura
from bbva_gastos.server import make_server
from test_db import _row


@pytest.fixture
def conn(monkeypatch):
    root = Path(__file__).parent / "_tmp"
    root.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="dashboard-", dir=root) as directory:
        path = Path(directory) / "dashboard.sqlite"
        monkeypatch.setenv("BBVA_GASTOS_DB", str(path))
        monkeypatch.setattr(db, "CORRECCIONES", Path(directory) / "correcciones.csv")
        connection = db.connect(path)
        yield connection
        connection.close()


@pytest.fixture
def api(conn):
    httpd = make_server("127.0.0.1", 0)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    worker.join(timeout=5)
    httpd.server_close()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def test_pagination_search_and_totals_include_more_than_2000_movements(conn, api):
    rows = [_row(concepto=f"Compra {i:04}", importe="-0.10", id=str(i)) for i in range(2051)]
    db.upsert_movements(conn, rows)
    first = get(api + "/api/movimientos?limit=50&orden=concepto&sentido=asc")
    last = get(api + "/api/movimientos?limit=50&orden=concepto&sentido=asc&offset=2050")
    assert first["total"] == last["total"] == 2051
    assert len(first["movimientos"]) == 50
    assert len(last["movimientos"]) == 1
    assert last["movimientos"][0]["concepto"] == "Compra 2050"
    assert first["resumen"] == {"ingresos": "0.00", "salidas": "205.10", "neto": "-205.10"}
    found = get(api + "/api/movimientos?texto=2050")
    assert found["total"] == 1
    assert found["total_periodo"] == 2051
    assert found["resumen"]["neto"] == "-0.10"


def test_combined_filters_search_accents_and_jev_review(conn):
    pending = _row(concepto="Café Álamo", importe="-2.00")
    reviewed = _row(concepto="Café Álamo", importe="-10.00")
    income = _row(concepto="Café Álamo", importe="5.00", direccion="ingreso")
    db.upsert_movements(conn, [pending, reviewed, income, _row(fecha="2026-02-01")])
    db.save_labels(conn, [{**pending, "categoria": "ocio", "origen": "jev", "confianza": 0.2, "revisar": True},
                         {**reviewed, "categoria": "ocio", "origen": "jev", "confianza": 0.9, "revisar": False}])
    result = db.query_movimientos(conn, "2026-01", texto="CAFE ALAMO", categoria="ocio",
                                  direccion="gasto", origen="jev", revisar=True)
    assert result["total"] == 1
    assert result["total_periodo"] == 3
    assert result["movimientos"][0]["importe"] == "-2.00"
    assert db.query_movimientos(conn, "2026-02", categoria="sin_clasificar")["total"] == 1


def test_sorting_is_numeric_and_page_boundaries_are_stable(conn):
    db.upsert_movements(conn, [_row(importe=n, id=str(i)) for i, n in enumerate(["-2.00", "-10.00", "100.00", "-2.00"])])
    all_rows = db.query_movimientos(conn, orden="importe", sentido="asc")["movimientos"]
    assert [r["importe"] for r in all_rows] == ["-10.00", "-2.00", "-2.00", "100.00"]
    first = db.query_movimientos(conn, orden="importe", sentido="asc", limit=2)
    last = db.query_movimientos(conn, orden="importe", sentido="asc", limit=2, offset=999)
    assert last["offset"] == 2
    assert first["movimientos"] + last["movimientos"] == all_rows
    empty = db.query_movimientos(conn, texto="inexistente", offset=50)
    assert empty["offset"] == empty["total"] == 0
    assert empty["resumen"]["neto"] == "0.00"


@pytest.mark.parametrize("query", ["mes=2026-1", "mes=0000-01", "mes=2026-13", "limit=x", "limit=0",
                                   "offset=-1", "orden=hash", "sentido=drop", "revisar=2", "origen=otro",
                                   "direccion=otro", "categoria=desconocida"])
def test_invalid_parameters_return_json_errors(api, query):
    with pytest.raises(urllib.error.HTTPError) as error:
        get(api + "/api/movimientos?" + query)
    assert error.value.code == 400
    assert json.load(error.value)["error"]


def test_export_uses_all_filtered_rows_and_escapes_formulas(conn, api):
    db.upsert_movements(conn, [_row(id=str(i), concepto="=SUM(A1:A2)", detalle="\t@algo", importe="-0.01") for i in range(55)])
    db.upsert_movements(conn, [_row(concepto="Otro", importe="20.00", direccion="ingreso")])
    with urllib.request.urlopen(api + "/api/movimientos.csv?direccion=gasto&limit=25&offset=25") as response:
        assert response.headers["Content-Type"].startswith("text/csv")
        assert "attachment" in response.headers["Content-Disposition"]
        data = response.read()
    assert data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))
    assert len(rows) == 55
    assert rows[0]["Concepto"] == "'=SUM(A1:A2)"
    assert rows[0]["Detalle"] == "'\t@algo"
    assert sum(Decimal(r["Importe"].replace(",", ".")) for r in rows) == Decimal("-0.55")


def test_month_comparison_requires_two_complete_months(conn):
    file = Path(conn.execute('PRAGMA database_list').fetchone()['file']).with_name('comparacion.csv')
    file.write_text('Fecha;Concepto;Importe\n14/03/2026;Mercadona;-2\n01/02/2026;Mercadona;-20\n01/01/2026;Mercadona;-10\n', encoding='utf-8')
    db.import_file(conn, file, desde='2026-01-01', hasta='2026-03-14')
    assert db.kpis(conn, "2026-02")["variacion_consumo"] == "100.0"
    assert db.kpis(conn, "2026-03")["variacion_consumo"] is None
    assert not any("respecto a" in s for s in db.insights(conn, "2026-03")["frases"])


def test_dashboard_and_empty_state(api):
    data = get(api + "/api/dashboard")
    assert data["kpis"]["movimientos"] == data["kpis"]["revisar_n"] == 0
    assert data["lectura"]["parrafos"] == []
    assert data["meses"]["meses"] == []
    with urllib.request.urlopen(api + "/app.js") as response:
        assert "javascript" in response.headers["Content-Type"]


def test_reading_sends_only_aggregates_and_handles_network_failures(conn, api, monkeypatch):
    secret = "Nombre privado de prueba"
    db.upsert_movements(conn, [_row(concepto=secret, detalle="Referencia privada 123")])
    monkeypatch.setattr(lectura, "load_api_key", lambda: "test-key")
    captured = []

    def fake_request(key, data):
        captured.append(data)
        return ["Resumen del periodo."]

    monkeypatch.setattr(lectura, "_pedir", fake_request)
    request = urllib.request.Request(api + "/api/lectura", method="POST")
    with urllib.request.urlopen(request) as response:
        assert json.load(response)["parrafos"]
    payload = json.dumps(captured)
    assert secret not in payload and "Referencia privada" not in payload
    assert "movimientos_mayores" not in payload
    assert "concepto" not in payload and "detalle" not in payload

    def disconnected(*args):
        raise requests.Timeout("test")

    monkeypatch.setattr(lectura, "_pedir", disconnected)
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(urllib.request.Request(api + "/api/lectura?nuevo=1", method="POST"))
    assert error.value.code == 400
    assert "OpenRouter" in json.load(error.value)["error"]


def test_empty_reading_never_calls_provider(conn, monkeypatch):
    monkeypatch.setattr(lectura, "load_api_key", lambda: pytest.fail("No se debe consultar la clave"))
    with pytest.raises(lectura.LecturaError, match="Carga movimientos"):
        lectura.generar(conn)


def statement(conn, name, rows=''):
    path = Path(conn.execute('PRAGMA database_list').fetchone()['file']).with_name(name + '.csv')
    path.write_text(f'{name}\nFecha;Concepto;Importe\n{rows}', encoding='utf-8')
    return path


def test_coverage_uses_declared_intervals_and_preserves_holes(conn):
    first = statement(conn, 'primero', '10/01/2026;Mercadona;-5\n')
    second = statement(conn, 'segundo')
    third = statement(conn, 'tercero', '10/03/2026;Mercadona;-2\n')
    db.import_file(conn, first)
    assert db.kpis(conn, '2026-01')['cobertura'] == 'desconocida'
    db.import_file(conn, first, desde='2026-01-01', hasta='2026-01-15')
    db.import_file(conn, third, desde='2026-03-01', hasta='2026-03-31')
    assert db.kpis(conn, '2026-01')['cobertura'] == 'parcial'
    assert db.kpis(conn, '2026-02')['cobertura'] == 'desconocida'
    assert db.evolucion(conn)[1]['sin_datos'] is True
    db.import_file(conn, second, desde='2026-01-16', hasta='2026-02-28')
    assert db.kpis(conn, 'todos')['cobertura'] == 'completa'
    assert db.kpis(conn, '2026-02')['movimientos'] == 0
    assert db.evolucion(conn)[1]['sin_datos'] is False
    assert db.kpis(conn, '2026-02')['variacion_consumo'] == '-100.0'
    assert db.estado(conn)['movimientos'] == 2


def test_coverage_cannot_union_two_accounts_or_claim_invalid_rows(conn):
    first = statement(conn, 'cuenta1', '10/01/2026;Mercadona;-5\n')
    second = statement(conn, 'cuenta2', '25/01/2026;Mercadona;-5\n')
    db.import_file(conn, first, desde='2026-01-01', hasta='2026-01-15', cuenta='A')
    db.import_file(conn, second, desde='2026-01-16', hasta='2026-01-31', cuenta='B')
    assert db.kpis(conn, '2026-01')['cobertura'] == 'parcial'
    invalid = statement(conn, 'mal', '01/01/2026;Mercadona;NaN\n')
    with pytest.raises(ValueError):
        db.import_file(conn, invalid, desde='2026-01-01', hasta='2026-01-31')
    assert len(db.meses(conn)['extractos']) == 2


def post(api, path, data, headers=None):
    query = urllib.request.Request(api + path, json.dumps(data).encode(),
                                   {'Content-Type': 'application/json', **(headers or {})})
    with urllib.request.urlopen(query) as response:
        return json.load(response)


def test_edit_category_updates_every_view_and_clear_restores_rule(conn, api):
    row = _row(concepto='Mercadona', importe='-10')
    db.upsert_movements(conn, [row])
    digest = db.movement_hash(row)
    post(api, '/api/categoria-manual', {'hash': digest, 'categoria': 'ahorro_inversion'})
    assert get(api + '/api/kpis')['consumo'] == '0.00'
    assert get(api + '/api/kpis')['ahorro'] == '10.00'
    assert get(api + '/api/movimientos?origen=manual')['total'] == 1
    with pytest.raises(urllib.error.HTTPError) as invalid:
        post(api, '/api/categoria-manual', {'hash': digest, 'categoria': 'nomina'})
    assert invalid.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as forbidden:
        post(api, '/api/categoria-manual', {'hash': digest, 'categoria': None}, {'Origin': 'https://example.org'})
    assert forbidden.value.code == 403
    post(api, '/api/categoria-manual', {'hash': digest, 'categoria': None})
    assert get(api + '/api/kpis')['consumo'] == '10.00'


def test_conciliation_both_decisions_and_undo(conn, api):
    db.import_file(conn, statement(conn, 'extracto', '10/01/2026;Mercadona;-5\n10/01/2026;Mercadona;-5\n'))
    bank = _row(lector='enable-banking', id='t1', cuenta='a', concepto='Compra en Mercadona', importe='-5')
    db.upsert_movements(conn, [bank])
    db.upsert_movements(conn, [{**bank, 'concepto': 'Mercadona actualizado'}])
    assert get(api + '/api/kpis')['consumo'] == '15.00'
    data = get(api + '/api/dashboard')['conciliacion']
    assert len(data['candidatos']) == 2  # Dos cargos legítimos idénticos del mismo extracto.
    pair = {key: val['hash'] for key, val in data['candidatos'][0].items()}
    post(api, '/api/conciliar', {**pair, 'accion': 'distintos'})
    assert len(get(api + '/api/dashboard')['conciliacion']['candidatos']) == 1
    post(api, '/api/conciliar', {**pair, 'accion': 'deshacer'})
    post(api, '/api/conciliar', {**pair, 'accion': 'duplicado', 'excluido': db.movement_hash(bank)})
    assert get(api + '/api/kpis')['consumo'] == '10.00'
    assert get(api + '/api/movimientos')['total'] == 2
    post(api, '/api/conciliar', {**pair, 'accion': 'deshacer'})
    assert get(api + '/api/kpis')['consumo'] == '15.00'


def test_local_portfolio_navigation_and_snapshot(conn, api, monkeypatch):
    file = Path(conn.execute('PRAGMA database_list').fetchone()['file']).with_name('privado.json')
    monkeypatch.setenv('BBVA_PATRIMONIO_JSON', str(file))
    with urllib.request.urlopen(api + '/inversion') as response:
        html = response.read().decode()
        assert 'data-patrimonio-local="true"' in html
        assert 'href="/"' in html
        assert 'cdn.jsdelivr.net' not in html
    with pytest.raises(urllib.error.HTTPError) as missing:
        get(api + '/api/inversion')
    assert missing.value.code == 404
    data = {'meta': {}, 'total': {'patrimonio': 1500}, 'perf': {}, 'mi': {}, 'crypto': {}, 'status': [],
            'spreadsheetUrl': 'https://example.org/privado'}
    file.write_text(json.dumps(data), encoding='utf-8')
    assert get(api + '/api/inversion')['total']['patrimonio'] == 1500
    assert 'spreadsheetUrl' not in get(api + '/api/inversion')

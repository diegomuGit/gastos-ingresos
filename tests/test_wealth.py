import json
import shutil
import subprocess
import urllib.error
from pathlib import Path

import pytest

from bbva_gastos import cli, db, wealth
from test_dashboard import api, conn, get, post
from test_db import _row
from test_transfers import _archive, _catalog, _decision


def quote(day, mi, crypto, **options):
    return {'d': day, 'mi': mi, 'cr': crypto, 'miFill': False, 'crFill': False, **options}


def test_selects_latest_common_day_without_mixing_valuations(conn, api, monkeypatch):
    payload = _catalog()
    payload['series'] = [quote('2026-02-01', 1000, 100), quote('2026-02-02', 1100, 100, crFill=True),
                         quote('2026-02-03', 1100, 300, miFill=True)]
    _archive(conn, monkeypatch, payload)
    for day, balance in [('2026-02-01', '400.01'), ('2026-02-02', '460'), ('2026-02-03', '500')]:
        wealth.save_bank_close(conn, day, balance)
    common = get(api + '/api/patrimonio')
    assert common['fecha'] == '2026-02-01'
    assert (common['bbva'], common['myinvestor'], common['cripto'], common['total']) == ('400.01', '1000.00', '100.00', '1500.01')
    missing = get(api + '/api/patrimonio?fecha=2026-02-02')
    assert missing['bbva'] == '460.00' and missing['cripto'] is None and missing['total'] is None
    with pytest.raises(urllib.error.HTTPError) as bad_date:
        get(api + '/api/patrimonio?fecha=2026-02-30')
    assert bad_date.value.code == 400


def test_evolution_only_returns_complete_common_closings_in_date_order(conn, api, monkeypatch):
    payload = _catalog()
    payload['series'] = [quote('2026-02-04', 1000, 110), quote('2026-02-02', 1000, 100, crFill=True),
                         quote('2026-02-03', 1000, 108), quote('2026-02-01', 950, 100)]
    _archive(conn, monkeypatch, payload)
    for day, balance in [('2026-02-04', '410'), ('2026-02-01', '400'), ('2026-02-02', '405')]:
        wealth.save_bank_close(conn, day, balance)
    result = get(api + '/api/patrimonio/evolucion')
    assert result['omitidos'] == 1  # El 3 no tiene saldo y el 2 no tiene valoración de cripto.
    assert result['puntos'] == [
        {'fecha': '2026-02-01', 'bbva': '400.00', 'myinvestor': '950.00', 'cripto': '100.00',
         'ajuste_traspasos': '0.00', 'total': '1450.00'},
        {'fecha': '2026-02-04', 'bbva': '410.00', 'myinvestor': '1000.00', 'cripto': '110.00',
         'ajuste_traspasos': '0.00', 'total': '1520.00'},
    ]
    latest = get(api + '/api/patrimonio')
    assert result['puntos'][-1] == {key: latest[key] for key in result['puntos'][-1]}


def test_evolution_without_portfolio_is_empty(conn, api, monkeypatch):
    path = _archive(conn, monkeypatch, _catalog())
    path.unlink()
    result = get(api + '/api/patrimonio/evolucion')
    assert result['puntos'] == [] and result['aviso']


def test_period_filters_common_dates_and_old_export_still_shows_change(conn, api, monkeypatch):
    payload = _catalog()
    payload['series'] = [quote(day, 1000, 100) for day in ('2025-12-31', '2026-01-31', '2026-02-28', '2026-03-31')]
    _archive(conn, monkeypatch, payload)
    for row, balance in zip(payload['series'], ('300', '400', '500', '600')):
        wealth.save_bank_close(conn, row['d'], balance)
    recent = get(api + '/api/patrimonio/evolucion?periodo=1m')
    assert [point['fecha'] for point in recent['puntos']] == ['2026-02-28', '2026-03-31']
    assert recent['desglose']['variacion'] == '100.00'
    assert recent['desglose']['ahorro'] is None and recent['desglose']['exportacion_anterior']
    ytd = get(api + '/api/patrimonio/evolucion?periodo=ytd')
    assert ytd['desglose']['desde'] == '2026-01-31'
    with pytest.raises(urllib.error.HTTPError) as invalid:
        get(api + '/api/patrimonio/evolucion?periodo=mal')
    assert invalid.value.code == 400


def test_period_attribution_excludes_internal_transfers_and_counts_flows_between_common_dates(conn, api, monkeypatch):
    node = shutil.which('node')
    if node is None:
        pytest.skip('La exportación de cartera requiere Node.')
    dates = ['2026-01-31', '2026-02-01', '2026-02-03', '2026-02-04', '2026-02-05']
    tabs = {
        'Historico': [['Fecha', 'Total'], *zip(dates, [1000, 1000, 1260, 1260, 1275])],
        'Flujos': [['Fecha', 'Tipo', 'Importe'], ['2026-02-03', 'Aportación', 250]],
        'Crypto': [['Fecha', 'Valor_EUR', 'Cantidad'], *zip(dates, [100, 100, 100, 135, 136], [1, 1, 1, 1.3, 1.3])],
        'Crypto_Movs': [['Fecha', 'Tipo', 'Cantidad_BTC', 'Total_EUR'], ['2026-02-04', 'Compra', .3, 30]],
    }
    script = "const {compute}=require('./dashboard_myinvestor/harness'); process.stdout.write(JSON.stringify(compute(JSON.parse(require('fs').readFileSync(0, 'utf8')))));"
    portfolio = json.loads(subprocess.check_output([node, '-e', script], input=json.dumps(tabs).encode(),
                                                    cwd=Path(__file__).resolve().parents[1], timeout=10))
    path = _archive(conn, monkeypatch, portfolio)
    db.upsert_movements(conn, [_row(fecha=dates[1], importe='-250', saldo='500', concepto='Traspaso')])
    for index, balance in [(0, '750'), (2, '500'), (4, '520')]:
        wealth.save_bank_close(conn, dates[index], balance)
    pair, = get(api + '/api/traspasos')['candidatos']
    post(api, '/api/conciliar-traspaso', _decision(pair, 'confirmar'))
    result = get(api + '/api/patrimonio/evolucion?periodo=1m')
    assert [p['fecha'] for p in result['puntos']] == [dates[i] for i in (0, 1, 2, 4)]
    assert result['desglose'] == {'desde': dates[0], 'hasta': dates[4], 'variacion': '81.00', 'ahorro': '50.00',
                                 'myinvestor': '25.00', 'cripto': '6.00', 'cripto_estimada': False,
                                 'exportacion_anterior': False}
    # El gráfico conserva los cierres, pero no atribuye el ahorro cuando falta un flujo intermedio.
    portfolio['flujosPatrimonio']['cripto'][3]['neto'] = None
    path.write_text(json.dumps(portfolio), encoding='utf-8')
    incomplete = get(api + '/api/patrimonio/evolucion')
    assert incomplete['puntos'] == result['puntos']
    assert incomplete['desglose']['myinvestor'] == '25.00'
    assert incomplete['desglose']['cripto'] is None and incomplete['desglose']['ahorro'] is None


def test_bank_close_respects_intraday_order_and_contiguous_statement_coverage(conn, monkeypatch):
    payload = _catalog()
    payload['series'] = [quote('2026-02-01', 0, 100), quote('2026-02-02', 0, 100), quote('2026-02-04', 0, 100)]
    _archive(conn, monkeypatch, payload)
    db.upsert_movements(conn, [_row(fecha='2026-02-01', importe='-10', saldo='140', concepto='B'),
                              _row(fecha='2026-02-01', importe='50', saldo='150', concepto='A', direccion='ingreso'),
                              _row(fecha='2026-02-03', importe='-1', saldo='', concepto='Sin saldo')])
    with conn:
        conn.execute("INSERT INTO ficheros(hash, nombre, cargado, movimientos, cuenta) VALUES ('test', 'demo', '', 3, '')")
        conn.execute("INSERT INTO cobertura_extractos VALUES ('test', '', '2026-02-01', '2026-02-04')")
    assert wealth.summary(conn, '2026-02-01')['bbva'] == '140.00'
    carried = wealth.summary(conn)
    assert carried['fecha'] == '2026-02-02' and carried['total'] == '240.00'
    assert carried['cuentas'][0]['fecha_saldo'] == '2026-02-01'
    assert wealth.summary(conn, '2026-02-04')['total'] is None  # Hubo un movimiento posterior sin saldo.
    with conn:
        conn.execute("UPDATE cobertura_extractos SET desde='2026-02-03'")
    assert wealth.summary(conn, '2026-02-02')['bbva'] is None  # El hueco no demuestra que el saldo se mantenga.


def test_all_accounts_are_required_and_zero_is_a_balance(conn, monkeypatch):
    payload = _catalog()
    payload['series'] = [quote('2026-02-03', 70, 0)]
    _archive(conn, monkeypatch, payload)
    wealth.save_bank_close(conn, '2026-02-03', '0', 'principal')
    wealth.save_bank_close(conn, '2026-02-02', '9', 'segunda')
    assert wealth.summary(conn)['total'] is None
    wealth.save_bank_close(conn, '2026-02-03', '-2.02', 'segunda')
    assert wealth.summary(conn)['total'] == '67.98'


def test_cli_records_and_corrects_a_bank_close(conn):
    assert cli.main(['saldo-bbva', '2026-02-03', '-20.10', '--cuenta', 'secundaria']) == 0
    assert cli.main(['saldo-bbva', '2026-02-03', '-21.25', '--cuenta', 'secundaria']) == 0
    assert [tuple(row) for row in conn.execute('SELECT cuenta, fecha, saldo FROM saldos_bbva')] == [
        ('secundaria', '2026-02-03', '-21.25')]


@pytest.mark.parametrize('bank_date,flow_date,expected', [('2026-02-01', '2026-02-03', '250.00'),
                                                         ('2026-02-03', '2026-02-01', '-250.00')])
def test_transfer_only_adjusts_between_settlement_dates(conn, api, monkeypatch, bank_date, flow_date, expected):
    payload = _catalog({'clave': 'aporte', 'fecha': flow_date, 'centimos': 25000})
    payload['series'] = [quote(day, 1250 if day >= flow_date else 1000, 100)
                         for day in ('2026-01-31', '2026-02-01', '2026-02-03')]
    path = _archive(conn, monkeypatch, payload)
    db.upsert_movements(conn, [_row(fecha=bank_date, importe='-250', concepto='Traspaso', saldo='500')])
    for day in ('2026-01-31', '2026-02-01', '2026-02-03'):
        wealth.save_bank_close(conn, day, '500' if day >= bank_date else '750')
    pair, = get(api + '/api/traspasos')['candidatos']
    post(api, '/api/conciliar-traspaso', _decision(pair, 'confirmar'))
    between = get(api + '/api/patrimonio?fecha=2026-02-01')
    assert between['ajuste_traspasos'] == expected
    assert between['total'] == '1850.00'
    history = get(api + '/api/patrimonio/evolucion')['puntos']
    assert [point['total'] for point in history] == ['1850.00'] * 3
    assert [point['ajuste_traspasos'] for point in history] == ['0.00', expected, '0.00']
    for day in ('2026-01-31', '2026-02-03'):
        result = get(api + '/api/patrimonio?fecha=' + day)
        assert result['total'] == '1850.00' and not result['traspasos']
    assert get(api + '/api/patrimonio')['fecha'] == '2026-02-03'
    payload['conciliacionBBVA']['aportaciones'] = []
    path.write_text(json.dumps(payload), encoding='utf-8')
    assert get(api + '/api/patrimonio?fecha=2026-02-01')['total'] is None
    revised = get(api + '/api/patrimonio/evolucion')['puntos']
    assert [point['fecha'] for point in revised] == ['2026-01-31', '2026-02-03']
    post(api, '/api/conciliar-traspaso', _decision(pair, 'deshacer'))
    assert get(api + '/api/patrimonio?fecha=2026-02-01')['ajuste_traspasos'] == '0.00'


def test_same_day_transfer_is_never_added_to_the_total(conn, api, monkeypatch):
    day = '2026-02-03'
    payload = _catalog({'clave': 'aporte', 'fecha': day, 'centimos': 25000})
    payload['series'] = [quote(day, 1250, 100)]
    _archive(conn, monkeypatch, payload)
    db.upsert_movements(conn, [_row(fecha=day, importe='-250', concepto='Traspaso', saldo='500')])
    pair, = get(api + '/api/traspasos')['candidatos']
    post(api, '/api/conciliar-traspaso', _decision(pair, 'confirmar'))
    result = get(api + '/api/patrimonio')
    assert result['total'] == '1850.00' and result['ajuste_traspasos'] == '0.00'

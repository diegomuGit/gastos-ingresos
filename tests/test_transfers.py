import json
import shutil
import subprocess
import urllib.error
from pathlib import Path

import pytest

from bbva_gastos import db
from test_dashboard import api, conn, get, post
from test_db import _row


def _archive(conn, monkeypatch, payload):
    path = Path(conn.execute('PRAGMA database_list').fetchone()['file']).with_name('patrimonio.json')
    monkeypatch.setenv('BBVA_PATRIMONIO_JSON', str(path))
    path.write_text(json.dumps(payload), encoding='utf-8')
    return path


def _bank(**changes):
    fields = {'fecha': '2026-01-31', 'concepto': 'Transferencia emitida', 'importe': '-250.00'}
    return _row(**(fields | changes))


def _decision(pair, action):
    return {'banco': pair['banco']['hash'], 'aporte': pair['aporte']['id'], 'accion': action}


def _catalog(*items):
    return {'total': {}, 'perf': {}, 'mi': {'flows': {'items': []}}, 'crypto': {}, 'status': [], 'meta': {},
            'conciliacionBBVA': {'version': 1, 'aportaciones': items}}


def test_cross_month_transfer_changes_consumption_but_not_investment_returns(conn, api, monkeypatch):
    node = shutil.which('node')
    if node is None:
        pytest.skip('La prueba conjunta usa el cálculo de MyInvestor en Node.')
    tabs = {'Historico': [['Fecha', 'Total'], ['2026-01-30', 1000], ['2026-02-02', 1260]],
            'Flujos': [['Fecha', 'Tipo', 'Importe', 'Nota'], ['2026-02-02', 'Aportación', 250, 'BBVA']]}
    script = "const {compute}=require('./dashboard_myinvestor/harness'); process.stdout.write(JSON.stringify(compute(JSON.parse(require('fs').readFileSync(0, 'utf8')))));"
    portfolio = json.loads(subprocess.check_output([node, '-e', script], input=json.dumps(tabs).encode(),
                                                    cwd=Path(__file__).resolve().parents[1], timeout=10))
    _archive(conn, monkeypatch, portfolio)
    outgoing = _bank()
    db.upsert_movements(conn, [outgoing, _bank(concepto='Metro', importe='-10')])
    digest = db.movement_hash(outgoing)
    post(api, '/api/categoria-manual', {'hash': digest, 'categoria': 'compras'})
    assert get(api + '/api/kpis')['consumo'] == '260.00'
    pair, = get(api + '/api/traspasos?mes=2026-02')['candidatos']
    assert pair['dias'] == 2 and pair['banco']['hash'] == digest
    post(api, '/api/conciliar-traspaso', _decision(pair, 'confirmar'))
    kpis = get(api + '/api/kpis')
    assert (kpis['consumo'], kpis['resultado'], kpis['traspasos_myinvestor'], kpis['ahorro']) == ('10.00', '-260.00', '250.00', '250.00')
    assert not get(api + '/api/traspasos')['candidatos']
    assert get(api + '/api/movimientos?origen=traspaso')['movimientos'][0]['categoria'] == 'ahorro_inversion'
    investment = get(api + '/api/inversion')
    assert investment['traspasosBBVA'] == 1
    assert investment['mi']['flows']['items'][0]['traspasoBBVA'] is True
    assert investment['perf'] == portfolio['perf']
    assert investment['mi']['flows']['total'] == 250
    assert next(p for p in investment['perf']['periods'] if p['key'] == 'inicio')['mi']['gain'] == 10
    post(api, '/api/conciliar-traspaso', _decision(pair, 'deshacer'))
    assert get(api + '/api/kpis')['consumo'] == '260.00'
    assert get(api + '/api/movimientos?origen=manual')['movimientos'][0]['categoria'] == 'compras'


def test_ambiguous_matches_reject_reusing_a_contribution_and_save_dismissals(conn, api, monkeypatch):
    _archive(conn, monkeypatch, _catalog({'clave': 'a', 'fecha': '2026-02-02', 'centimos': 25000}))
    db.upsert_movements(conn, [_bank(), _bank(concepto='Segunda salida'),
                              _bank(concepto='Fuera de plazo', fecha='2026-02-08'),
                              _bank(concepto='Otro importe', importe='-249.99')])
    a, b = get(api + '/api/traspasos')['candidatos']
    assert a['ambigua'] and b['ambigua']
    post(api, '/api/conciliar-traspaso', _decision(b, 'descartar'))
    assert not get(api + '/api/traspasos')['candidatos'][0]['ambigua']
    post(api, '/api/conciliar-traspaso', _decision(a, 'confirmar'))
    with pytest.raises(urllib.error.HTTPError) as error:
        post(api, '/api/conciliar-traspaso', _decision(b, 'confirmar'))
    assert error.value.code == 400
    post(api, '/api/conciliar-traspaso', _decision(a, 'deshacer'))
    assert len(get(api + '/api/traspasos')['candidatos']) == 1
    post(api, '/api/conciliar-traspaso', _decision(b, 'deshacer'))
    assert len(get(api + '/api/traspasos')['candidatos']) == 2


def test_confirmed_match_can_be_undone_even_when_archive_is_missing(conn, api, monkeypatch):
    path = _archive(conn, monkeypatch, _catalog({'clave': 'a', 'fecha': '2026-02-02', 'centimos': 25000}))
    db.upsert_movements(conn, [_bank()])
    pair, = get(api + '/api/traspasos')['candidatos']
    post(api, '/api/conciliar-traspaso', _decision(pair, 'confirmar'))
    path.rename(path.with_name('anterior.json'))
    info = get(api + '/api/traspasos')
    assert info['aviso'] and not info['decisiones'][0]['verificado']
    assert get(api + '/api/kpis')['traspasos_myinvestor_n'] == 1
    post(api, '/api/conciliar-traspaso', _decision(pair, 'deshacer'))
    assert get(api + '/api/kpis')['traspasos_myinvestor_n'] == 0

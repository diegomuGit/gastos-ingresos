"""Revisión de traspasos BBVA → MyInvestor. El cálculo de cartera ya ingiere cada aporte."""

import hashlib
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

from bbva_gastos.db import _now, report_rows
from bbva_gastos.portfolio import snapshot

WINDOW_DAYS = 5


def _day(value):
    if not isinstance(value, str):
        return None
    try:
        day = date.fromisoformat(value)
    except ValueError:
        return None
    return day if day.isoformat() == value else None


def _id(key):
    return hashlib.sha256(key.encode('utf-8')).hexdigest()


def contributions(portfolio):
    block = portfolio.get('conciliacionBBVA')
    if not isinstance(block, dict) or block.get('version') != 1 or not isinstance(block.get('aportaciones'), list):
        raise ValueError('Vuelve a generar data/patrimonio.json para habilitar la conciliación de traspasos.')
    output = {}
    for item in block['aportaciones']:
        if not isinstance(item, dict) or not isinstance(item.get('clave'), str) or not _day(item.get('fecha')):
            raise ValueError('El catálogo de aportaciones no es válido. Vuelve a generar la exportación.')
        cents = item.get('centimos')
        key = _id(item['clave'])
        if type(cents) is not int or cents <= 0 or key in output:
            raise ValueError('El catálogo de aportaciones no es válido. Vuelve a generar la exportación.')
        output[key] = {'id': key, 'fecha': item['fecha'], 'importe': str(Decimal(cents).scaleb(-2)),
                       'origen': str(item.get('origen') or ''), 'nota': str(item.get('nota') or '')}
    return output


def _eligible(bank):
    return (bank['direccion'] == 'gasto' and bank['divisa'] == 'EUR' and _day(bank['fecha'])
            and Decimal(bank['importe']) < 0)


def _gap(bank, contribution):
    if _eligible(bank) and Decimal(bank['importe']) == -Decimal(contribution['importe']):
        gap = abs((_day(contribution['fecha']) - _day(bank['fecha'])).days)
        if gap <= WINDOW_DAYS:
            return gap
    return None


def _display(bank):
    return {key: bank[key] for key in ('hash', 'fecha', 'importe', 'concepto', 'detalle', 'categoria')}


def _active(bank, contribution, decision):
    return (bank is not None and contribution is not None
            and bank['fecha'] == decision['fecha_banco'] and bank['importe'] == decision['importe_banco']
            and contribution['fecha'] == decision['fecha_aporte'] and _gap(bank, contribution) is not None)


def confirmed_transfers(conn, portfolio, banks):
    decisions = conn.execute("SELECT * FROM traspasos_inversion WHERE estado='confirmado'").fetchall()
    if not decisions:
        return []
    try:
        flows = contributions(portfolio)
    except ValueError:
        flows = {}
    return [(row, _active(banks.get(row['banco']), flows.get(row['aporte']), row)) for row in decisions]


def review(conn, mes='todos'):
    error = None
    try:
        flows = contributions(snapshot())
    except ValueError as exc:
        error, flows = str(exc), {}
    banks = {row['hash']: row for row in report_rows(conn)}
    history = [dict(row) for row in conn.execute('SELECT * FROM traspasos_inversion ORDER BY decidido DESC, banco, aporte')]
    confirmed_banks = {row['banco'] for row in history if row['estado'] == 'confirmado'}
    confirmed_flows = {row['aporte'] for row in history if row['estado'] == 'confirmado'}
    decided = {(row['banco'], row['aporte']) for row in history}
    by_amount = defaultdict(list)
    for flow in flows.values():
        if flow['id'] not in confirmed_flows:
            by_amount[Decimal(flow['importe'])].append(flow)

    candidates = []
    for bank in banks.values():
        if bank['hash'] in confirmed_banks or not _eligible(bank):
            continue
        for flow in by_amount[-Decimal(bank['importe'])]:
            if (bank['hash'], flow['id']) in decided:
                continue
            gap = _gap(bank, flow)
            if gap is not None:
                candidates.append({'banco': _display(bank), 'aporte': flow, 'dias': gap})
    counts_b = Counter(pair['banco']['hash'] for pair in candidates)
    counts_f = Counter(pair['aporte']['id'] for pair in candidates)
    for pair in candidates:
        pair['ambigua'] = counts_b[pair['banco']['hash']] > 1 or counts_f[pair['aporte']['id']] > 1
    candidates.sort(key=lambda pair: (pair['banco']['categoria'] != 'ahorro_inversion', pair['ambigua'], pair['dias'],
                                      pair['banco']['fecha'], pair['banco']['hash'], pair['aporte']['id']))

    decisions = []
    for row in history:
        bank, flow = banks.get(row['banco']), flows.get(row['aporte'])
        if bank is None:
            bank_data = {'hash': row['banco'], 'fecha': row['fecha_banco'], 'importe': row['importe_banco'],
                         'concepto': 'Apunte excluido', 'detalle': '', 'categoria': None}
        else:
            bank_data = _display(bank)
        flow_data = flow or {'id': row['aporte'], 'fecha': row['fecha_aporte'],
                             'importe': str(-Decimal(row['importe_banco'])), 'origen': row['origen_aporte'], 'nota': ''}
        decisions.append({'banco': bank_data, 'aporte': flow_data, 'estado': row['estado'],
                          'verificado': _active(bank, flow, row), 'fecha_guardada': row['fecha_banco']})

    def included(pair):
        return mes == 'todos' or any(value.startswith(mes) for value in
                                     (pair['banco']['fecha'], pair['aporte']['fecha'], pair.get('fecha_guardada', '')))

    return {'aviso': error, 'candidatos': [p for p in candidates if included(p)],
            'decisiones': [p for p in decisions if included(p)]}


def decide(conn, bank_id, flow_id, action):
    if action not in ('confirmar', 'descartar', 'deshacer'):
        raise ValueError('Decisión de traspaso no válida.')
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if action == 'deshacer':
            conn.execute('DELETE FROM traspasos_inversion WHERE banco=? AND aporte=?', (bank_id, flow_id))
            return

        flow = contributions(snapshot()).get(flow_id)
        bank = next((row for row in report_rows(conn) if row['hash'] == bank_id), None)
        if bank is None or flow is None or _gap(bank, flow) is None:
            raise ValueError('La coincidencia ya no es válida. Actualiza el panel y vuelve a revisarla.')
        if conn.execute("""SELECT 1 FROM traspasos_inversion WHERE estado='confirmado'
                            AND (banco=? OR aporte=?)""", (bank_id, flow_id)).fetchone():
            raise ValueError('Uno de los apuntes ya está enlazado. Deshaz primero ese enlace.')
        conn.execute("""INSERT INTO traspasos_inversion VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(banco, aporte) DO UPDATE SET estado=excluded.estado, fecha_banco=excluded.fecha_banco,
                            importe_banco=excluded.importe_banco, fecha_aporte=excluded.fecha_aporte,
                            origen_aporte=excluded.origen_aporte, decidido=excluded.decidido""",
                     (bank_id, flow_id, 'confirmado' if action == 'confirmar' else 'descartado',
                      bank['fecha'], bank['importe'], flow['fecha'], flow['origen'], _now()))


def annotate_portfolio(conn, portfolio):
    banks = {row['hash']: row for row in report_rows(conn)}
    linked = {row['aporte'] for row, verified in confirmed_transfers(conn, portfolio, banks) if verified}
    if not linked:
        return portfolio
    for flow in portfolio['mi'].get('flows', {}).get('items', []):
        key = flow.get('claveBBVA')
        if isinstance(key, str) and _id(key) in linked:
            flow['traspasoBBVA'] = True
    portfolio['traspasosBBVA'] = len(linked)
    return portfolio

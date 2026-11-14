"""Patrimonio a una fecha común, con saldos de cierre y sin valoraciones arrastradas."""

from bisect import bisect_right
from calendar import monthrange
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from bbva_gastos.db import money, report_rows
from bbva_gastos.portfolio import snapshot
from bbva_gastos.transfers import confirmed_transfers

ZERO = Decimal('0')
PERIOD_MONTHS = {'1m': 1, '3m': 3, '6m': 6, '1a': 12}


def closing_date(value):
    try:
        day = date.fromisoformat(value)
    except (ValueError, TypeError) as error:
        raise ValueError('La fecha debe ser válida y tener formato AAAA-MM-DD.') from error
    if day.isoformat() != value:
        raise ValueError('La fecha debe tener formato AAAA-MM-DD.')
    return day


def _amount(value, *, round_cents=True):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        if number.is_finite() and abs(number) < Decimal('1e18'):
            return Decimal(money(number)) if round_cents else number
    except InvalidOperation:
        pass
    return None


def save_bank_close(conn, day, balance, account=''):
    closing_date(day)
    amount = _amount(balance)
    if amount is None:
        raise ValueError('El saldo debe ser un importe válido en euros, con punto decimal.')
    with conn:
        conn.execute("""INSERT INTO saldos_bbva VALUES (?, ?, ?)
                        ON CONFLICT(cuenta, fecha) DO UPDATE SET saldo=excluded.saldo""", (account, day, money(amount)))


def _valuations(portfolio):
    result = {key: {} for key in ('mi', 'cr')}
    series = portfolio.get('series')
    if not isinstance(series, list):
        return result
    for row in series:
        if not isinstance(row, dict):
            continue
        try:
            day = closing_date(row.get('d'))
        except ValueError:
            continue
        for key, carried in [('mi', 'miFill'), ('cr', 'crFill')]:
            amount = _amount(row.get(key))
            if amount is not None and row.get(carried) is False:
                result[key][day] = amount
    return result


def _day_close(rows):
    """El cierre es el saldo terminal de la cadena de apuntes, no el último hash."""
    amounts = [(_amount(row['importe']), _amount(row['saldo'])) for row in rows]
    if any(amount is None or balance is None for amount, balance in amounts):
        return None
    if len(amounts) == 1:
        return amounts[0][1]
    # El balance anterior a un apunte es su saldo menos su importe. La cadena
    # debe permitir recorrer todos los apuntes desde un inicio hasta un cierre.
    degrees, adjacent = defaultdict(int), defaultdict(set)
    for amount, balance in amounts:
        before = balance - amount
        degrees[before] += 1
        degrees[balance] -= 1
        adjacent[before].add(balance)
        adjacent[balance].add(before)
    starts = [node for node, degree in degrees.items() if degree == 1]
    ends = [node for node, degree in degrees.items() if degree == -1]
    if len(starts) != 1 or len(ends) != 1 or any(abs(degree) > 1 for degree in degrees.values()):
        return None  # Una cadena cerrada con varias posibilidades no identifica el cierre.
    pending, seen = starts[:], set()
    while pending:
        node = pending.pop()
        if node not in seen:
            seen.add(node)
            pending.extend(adjacent[node] - seen)
    return ends[0] if len(seen) == len(degrees) else None


def _bank_data(conn, rows):
    days, coverage = defaultdict(lambda: defaultdict(list)), defaultdict(list)
    for row in rows:
        try:
            day = closing_date(row['fecha'])
        except ValueError:
            continue
        days[row['cuenta']][day].append(row)
    for row in conn.execute('SELECT cuenta, desde, hasta FROM cobertura_extractos ORDER BY desde'):
        coverage[row['cuenta']].append((closing_date(row['desde']), closing_date(row['hasta'])))
    recorded = defaultdict(dict)
    for account, account_days in days.items():
        for day, movements in account_days.items():
            balance = _day_close(movements) if all(row['divisa'] == 'EUR' for row in movements) else None
            recorded[account][day] = (balance, 'extracto')
    for row in conn.execute('SELECT cuenta, fecha, saldo FROM saldos_bbva'):
        recorded[row['cuenta']][closing_date(row['fecha'])] = (Decimal(row['saldo']), 'manual')
    return {account: (sorted(recorded[account]), coverage[account], recorded[account])
            for account in sorted(recorded.keys() | coverage.keys())}


def _covered(intervals, first, last):
    cursor = first.toordinal() + 1
    for start, end in intervals:
        if end.toordinal() < cursor:
            continue
        if start.toordinal() > cursor:
            break
        cursor = end.toordinal() + 1
        if cursor > last.toordinal():
            return True
    return False


def _bank_close(data, day):
    result = []
    for account, (days, intervals, recorded) in data.items():
        # Un movimiento posterior sin saldo invalida la prolongación del cierre.
        index = bisect_right(days, day)
        anchor = days[index - 1] if index else None
        balance, source = None, None
        if anchor and (anchor == day or _covered(intervals, anchor, day)):
            balance, source = recorded[anchor]
        result.append({'cuenta': account, 'saldo': money(balance) if balance is not None else None,
                       'fecha_saldo': anchor.isoformat() if balance is not None else None,
                       'fuente': source if balance is not None else None})
    return result


def _transfers_by_day(conn, portfolio, rows):
    adjustments, unresolved = defaultdict(list), set()
    for transfer, verified in confirmed_transfers(conn, portfolio, {row['hash']: row for row in rows}):
        bank_day, fund_day = (closing_date(transfer[key]) for key in ('fecha_banco', 'fecha_aporte'))
        amount = abs(Decimal(transfer['importe_banco']))
        entry = {'fecha_salida': bank_day.isoformat(), 'fecha_abono': fund_day.isoformat(),
                 'importe': money(amount if bank_day < fund_day else -amount),
                 'tipo': 'en_transito' if bank_day < fund_day else 'solapado'}
        start, end = sorted((bank_day.toordinal(), fund_day.toordinal()))
        for ordinal in range(start, end):
            day = date.fromordinal(ordinal)
            if verified:
                adjustments[day].append(entry)
            else:
                unresolved.add(day)
    return adjustments, unresolved


def _inputs(conn):
    rows = report_rows(conn)
    bank = _bank_data(conn, rows)
    try:
        portfolio = snapshot()
        portfolio_error = None
    except ValueError as error:
        portfolio, portfolio_error = {}, str(error)
    return bank, _valuations(portfolio), _transfers_by_day(conn, portfolio, rows), portfolio_error, portfolio.get('flujosPatrimonio')


def _closing(day, bank, quotes, transfers):
    accounts = _bank_close(bank, day) if day else []
    bbva = sum((Decimal(a['saldo']) for a in accounts), ZERO) if accounts and all(a['saldo'] is not None for a in accounts) else None
    mi, crypto = (quotes[key].get(day) for key in ('mi', 'cr'))
    by_day, unresolved = transfers
    adjustments = by_day.get(day, [])
    adjustment = sum((Decimal(row['importe']) for row in adjustments), ZERO)
    total = bbva + mi + crypto + adjustment if all(x is not None for x in (bbva, mi, crypto)) and day not in unresolved else None
    return {'fecha': day.isoformat() if day else None, 'cuentas': accounts,
            'bbva': money(bbva) if bbva is not None else None,
            'myinvestor': money(mi) if mi is not None else None, 'cripto': money(crypto) if crypto is not None else None,
            'ajuste_traspasos': money(adjustment), 'traspasos': adjustments,
            'total': money(total) if total is not None else None}


def summary(conn, requested=None):
    bank, quotes, transfers, portfolio_error, _ = _inputs(conn)
    bank_days = {day for days, _, _ in bank.values() for day in days}
    if requested is not None:
        day = closing_date(requested)
        result = _closing(day, bank, quotes, transfers)
    else:
        # El cierre por defecto coincide con el último punto que se puede graficar.
        latest_with_balances = None
        for day in sorted(quotes['mi'].keys() & quotes['cr'].keys(), reverse=True):
            result = _closing(day, bank, quotes, transfers)
            if result['total'] is not None:
                break
            if latest_with_balances is None and result['bbva'] is not None:
                latest_with_balances = day, result
        else:
            if latest_with_balances:
                day, result = latest_with_balances
            else:
                day = max(bank_days | quotes['mi'].keys() | quotes['cr'].keys(), default=None)
                result = _closing(day, bank, quotes, transfers)

    notices = [portfolio_error] if portfolio_error else []
    if day in transfers[1]:
        notices.append('Hay un traspaso conciliado pendiente de verificar que afecta a este cierre.')
    if result['bbva'] is None:
        notices.append('Falta un saldo de cierre verificable de BBVA para esta fecha.')
    mi, crypto = result['myinvestor'], result['cripto']
    if mi is None or crypto is None:
        missing = 'MyInvestor y cripto' if mi is None and crypto is None else 'MyInvestor' if mi is None else 'cripto'
        notices.append(f'Falta una valoración de {missing} en esa fecha.')
    latest = {key: max(values).isoformat() if values else None for key, values in
              [('bbva', bank_days), ('myinvestor', quotes['mi']), ('cripto', quotes['cr'])]}
    return {**result, 'ultimo': latest, 'avisos': notices}


def _period_start(period, last):
    if period == 'todo':
        return date.min
    if period == 'ytd':
        return date(last.year, 1, 1)
    year, month = divmod(last.year * 12 + last.month - 1 - PERIOD_MONTHS[period], 12)
    if year < 1:
        return date.min
    month += 1
    return date(year, month, min(last.day, monthrange(year, month)[1]))


def _market_gain(quotes, flows, first, last, change):
    if not isinstance(flows, list):
        return None, False
    by_date = {entry.get('fecha'): entry for entry in flows
               if isinstance(entry, dict) and isinstance(entry.get('fecha'), str)}
    in_period = [by_date.get(day.isoformat(), {}) for day in quotes if first < day <= last]
    amounts = [_amount(entry.get('neto'), round_cents=False) for entry in in_period]
    if any(amount is None for amount in amounts):
        return None, False
    return Decimal(money(change - sum(amounts, ZERO))), any(entry.get('estimado') is True for entry in in_period)


def _period_breakdown(points, quotes, flows):
    if len(points) < 2:
        return None
    first, last = points[0], points[-1]
    start, end = closing_date(first['fecha']), closing_date(last['fecha'])
    delta = Decimal(last['total']) - Decimal(first['total'])
    current_export = isinstance(flows, dict) and flows.get('version') == 1
    market = {}
    estimated = False
    for key, quote_key in [('myinvestor', 'mi'), ('cripto', 'cr')]:
        change = Decimal(last[key]) - Decimal(first[key])
        gain, inferred = _market_gain(quotes[quote_key], flows.get(key) if current_export else None, start, end, change)
        market[key] = gain
        estimated |= inferred
    savings = delta - sum(market.values(), ZERO) if all(value is not None for value in market.values()) else None
    return {'desde': first['fecha'], 'hasta': last['fecha'], 'variacion': money(delta),
            'ahorro': money(savings) if savings is not None else None,
            'myinvestor': money(market['myinvestor']) if market['myinvestor'] is not None else None,
            'cripto': money(market['cripto']) if market['cripto'] is not None else None,
            'cripto_estimada': estimated, 'exportacion_anterior': not current_export}


def evolution(conn, period='todo'):
    """Solo cierres completos y verificables; los días omitidos nunca se rellenan."""
    if period not in PERIOD_MONTHS.keys() | {'todo', 'ytd'}:
        raise ValueError('Periodo de patrimonio no válido.')
    bank, quotes, transfers, portfolio_error, flows = _inputs(conn)
    points, omitted = [], []
    for day in sorted(quotes['mi'].keys() & quotes['cr'].keys()):
        point = _closing(day, bank, quotes, transfers)
        if point['total'] is None:
            omitted.append(day)
            continue
        points.append({key: point[key] for key in ('fecha', 'bbva', 'myinvestor', 'cripto', 'ajuste_traspasos', 'total')})
    if points and period != 'todo':
        start = _period_start(period, closing_date(points[-1]['fecha']))
        points = [point for point in points if point['fecha'] >= start.isoformat()]
        omitted = [day for day in omitted if day >= start]
    return {'puntos': points, 'omitidos': len(omitted), 'aviso': portfolio_error,
            'desglose': _period_breakdown(points, quotes, flows)}

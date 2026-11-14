"""SQLite local. El panel lee de aquí; el HTML no lleva cifras del extracto."""

import csv
import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from bbva_gastos.categories import ETIQUETAS, GASTO, INGRESO, etiquetar, match_rule
from bbva_gastos.client import DATA
from bbva_gastos.statement import balance_breaks, load_statement

DB_PATH = DATA / "gastos.sqlite"
CORRECCIONES = DATA / "correcciones.csv"
ENTRADA = DATA.parent / "entrada"
MOVIMIENTOS_JSON = DATA / "movimientos.json"

MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
INGRESO_CATS = set(INGRESO)
CONSUMO_EXCL = {"ahorro_inversion", "bizum_personas"}
SIN = {"", "sin_clasificar", "sin_categoria"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS movimientos (
    hash TEXT PRIMARY KEY,
    fecha TEXT NOT NULL,
    fecha_valor TEXT NOT NULL DEFAULT '',
    concepto TEXT NOT NULL,
    detalle TEXT NOT NULL DEFAULT '',
    importe TEXT NOT NULL,
    saldo TEXT NOT NULL DEFAULT '',
    divisa TEXT NOT NULL DEFAULT 'EUR',
    direccion TEXT NOT NULL,
    categoria TEXT,
    confianza REAL,
    origen_clasificacion TEXT NOT NULL DEFAULT 'pendiente',
    revisar INTEGER NOT NULL DEFAULT 1,
    lector TEXT NOT NULL DEFAULT '',
    fichero TEXT NOT NULL DEFAULT '',
    externo TEXT NOT NULL DEFAULT '',
    actualizado TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS movimientos_fecha ON movimientos(fecha);
CREATE TABLE IF NOT EXISTS ficheros (
    hash TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    cargado TEXT NOT NULL,
    movimientos INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cobertura_extractos (
    fichero TEXT PRIMARY KEY REFERENCES ficheros(hash),
    cuenta TEXT NOT NULL,
    desde TEXT NOT NULL,
    hasta TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conciliacion (
    primero TEXT NOT NULL REFERENCES movimientos(hash),
    segundo TEXT NOT NULL REFERENCES movimientos(hash),
    excluido TEXT CHECK (excluido IS NULL OR excluido IN (primero, segundo)),
    decidido TEXT NOT NULL,
    PRIMARY KEY (primero, segundo)
);
CREATE UNIQUE INDEX IF NOT EXISTS conciliacion_excluido ON conciliacion(excluido);
CREATE TABLE IF NOT EXISTS traspasos_inversion (
    banco TEXT NOT NULL REFERENCES movimientos(hash),
    aporte TEXT NOT NULL,
    estado TEXT NOT NULL CHECK (estado IN ('confirmado', 'descartado')),
    fecha_banco TEXT NOT NULL,
    importe_banco TEXT NOT NULL,
    fecha_aporte TEXT NOT NULL,
    origen_aporte TEXT NOT NULL,
    decidido TEXT NOT NULL,
    PRIMARY KEY (banco, aporte)
);
CREATE UNIQUE INDEX IF NOT EXISTS traspasos_banco ON traspasos_inversion(banco) WHERE estado='confirmado';
CREATE UNIQUE INDEX IF NOT EXISTS traspasos_aporte ON traspasos_inversion(aporte) WHERE estado='confirmado';
CREATE TABLE IF NOT EXISTS saldos_bbva (
    cuenta TEXT NOT NULL,
    fecha TEXT NOT NULL,
    saldo TEXT NOT NULL,
    PRIMARY KEY(cuenta, fecha)
);
CREATE TABLE IF NOT EXISTS lecturas (
    clave TEXT PRIMARY KEY, mes TEXT NOT NULL, modelo TEXT NOT NULL,
    parrafos TEXT NOT NULL, creado TEXT NOT NULL
);
"""


def connect(path=None):
    chosen = Path(path or os.environ.get("BBVA_GASTOS_DB") or DB_PATH)
    chosen.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(chosen)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    migration = {"movimientos": {"cuenta": "TEXT NOT NULL DEFAULT ''", "categoria_manual": "TEXT",
                                 "extracto": "TEXT NOT NULL DEFAULT ''"},
                 "ficheros": {"cuenta": "TEXT NOT NULL DEFAULT ''"}}
    for table, additions in migration.items():
        if additions.keys() <= {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}:
            continue
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}
            for name, definition in additions.items():
                if name not in columns:
                    conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    return conn


def money(value):
    if value is None or value == "":
        return ""
    return format(Decimal(str(value)).quantize(Decimal("0.01")), "f")


def euros(value):
    amount = Decimal(str(value)).quantize(Decimal("0.01"))
    sign = "−" if amount < 0 else ("+" if amount > 0 else "")
    whole, cents = f"{abs(amount):.2f}".split(".")
    grouped = f"{int(whole):,}".replace(",", ".")
    return f"{sign}{grouped},{cents} €"


def movement_hash(row):
    saldo = row.get("saldo")
    saldo_text = money(saldo) if saldo not in (None, "") else ""
    externo = str(row.get("id") or row.get("externo") or "")
    cuenta = row.get("cuenta") or ""
    divisa = row.get("divisa") or "EUR"
    if cuenta and externo and row.get("lector") == "enable-banking":
        identity = json.dumps(["enable-banking", cuenta, externo], ensure_ascii=False)
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()
    payload = "|".join(
        [
            str(row.get("fecha") or ""),
            money(row.get("importe")),
            saldo_text,
            " ".join(str(row.get("concepto") or "").casefold().split()),
            " ".join(str(row.get("detalle") or "").casefold().split()),
            externo,
        ]
    )
    source = row.get('extracto') if not saldo_text else None
    if cuenta or divisa != "EUR" or source or row.get("ocurrencia"):
        payload = json.dumps([payload, cuenta, divisa, source, row.get("ocurrencia", 0)], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_movements(conn, rows, *, commit=True):
    now = _now()
    inserted = 0
    updated = 0
    for row in rows:
        if not row.get("fecha"):
            continue
        digest = movement_hash(row)
        if row.get('lector') == 'enable-banking' and row.get('cuenta') and row.get('id'):
            known = conn.execute("SELECT hash FROM movimientos WHERE lector=? AND cuenta=? AND externo=?",
                                 ('enable-banking', row['cuenta'], row['id'])).fetchone()
            if not known and row.get('fichero'):
                legacy = movement_hash({**row, 'cuenta': ''})
                known = conn.execute("SELECT hash FROM movimientos WHERE hash=? AND cuenta='' AND lector=? AND fichero=?",
                                     (legacy, 'enable-banking', row['fichero'])).fetchone()
            if known:
                digest = known['hash']
        found = conn.execute("SELECT 1 FROM movimientos WHERE hash = ?", (digest,)).fetchone()
        values = (
            row.get("fecha"),
            row.get("fecha_valor") or "",
            row.get("concepto") or "",
            row.get("detalle") or "",
            money(row.get("importe")),
            money(row.get("saldo")) if row.get("saldo") not in (None, "") else "",
            row.get("divisa") or "EUR",
            row.get("direccion"),
            row.get("lector") or "",
            row.get("fichero") or "",
            str(row.get("id") or row.get("externo") or ""),
            now,
            row.get("cuenta") or "",
            row.get("extracto") or "",
            digest,
        )
        if found:
            conn.execute(
                """UPDATE movimientos
                   SET fecha=?, fecha_valor=?, concepto=?, detalle=?, importe=?, saldo=?,
                       divisa=?, direccion=?, lector=?, fichero=?, externo=?, actualizado=?, cuenta=?, extracto=?
                   WHERE hash=?""",
                values,
            )
            updated += 1
        else:
            conn.execute(
                """INSERT INTO movimientos (
                       fecha, fecha_valor, concepto, detalle, importe, saldo, divisa,
                       direccion, lector, fichero, externo, actualizado, cuenta, extracto, hash,
                       origen_clasificacion, revisar
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pendiente',1)""",
                values,
            )
            inserted += 1
    if commit:
        conn.commit()
    return inserted, updated


def load_corrections(path=None):
    file = Path(path) if path else CORRECCIONES
    if path is None and not file.exists():
        return []
    if path is not None and not file.exists():
        raise ValueError(f"No está el fichero de correcciones: {file}")
    allowed = set(GASTO) | set(INGRESO)
    found = []
    with file.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = {name.strip() for name in (reader.fieldnames or [])}
        if "patron" not in fields or "categoria" not in fields:
            raise ValueError("correcciones.csv necesita las columnas patron y categoria.")
        for line in reader:
            patron = (line.get("patron") or "").strip()
            categoria = (line.get("categoria") or "").strip()
            if not patron or patron.startswith("#"):
                continue
            if categoria not in allowed:
                known = ", ".join(sorted(allowed))
                raise ValueError(f"La categoría {categoria!r} no existe. Usa una de: {known}.")
            try:
                re.compile(patron, re.I)
            except re.error as error:
                raise ValueError(f"La expresión {patron!r} no es una expresión regular válida: {error}") from error
            found.append((patron, categoria))
    return found


def _label_of(row, corrections):
    movement = dict(row)
    allowed = set(INGRESO) if movement["direccion"] == "ingreso" else set(GASTO)
    text = f"{movement['concepto']} {movement['detalle'] or ''}"
    for patron, categoria in corrections:
        if categoria not in allowed:
            continue
        if re.search(patron, text, re.I):
            return categoria, 1.0, "correccion", 0
    if movement["origen_clasificacion"] == "jev" and movement["categoria"]:
        return None
    rule = match_rule(movement)
    if rule:
        return rule, 1.0, "regla", 0
    return None, None, "pendiente", 1


def apply_local(conn, corrections=None, *, commit=True):
    chosen = load_corrections() if corrections is None else corrections
    rows = conn.execute("SELECT * FROM movimientos").fetchall()
    for row in rows:
        label = _label_of(row, chosen)
        if label is None:
            continue
        categoria, confianza, origen, revisar = label
        current = (
            row["categoria"],
            row["confianza"],
            row["origen_clasificacion"],
            row["revisar"],
        )
        nxt = (categoria, confianza, origen, revisar)
        if current == nxt:
            continue
        conn.execute(
            """UPDATE movimientos
               SET categoria=?, confianza=?, origen_clasificacion=?, revisar=?
               WHERE hash=?""",
            (categoria, confianza, origen, revisar, row["hash"]),
        )
    if commit:
        conn.commit()


def save_labels(conn, rows):
    for row in rows:
        origen = row.get("origen")
        if origen not in {"jev", "regla"}:
            continue
        digest = row.get("hash") or movement_hash(row)
        conn.execute(
            """UPDATE movimientos
               SET categoria=?, confianza=?, origen_clasificacion=?, revisar=?
               WHERE hash=? AND origen_clasificacion != 'correccion'""",
            (
                row.get("categoria"),
                row.get("confianza"),
                origen,
                1 if row.get("revisar") else 0,
                digest,
            ),
        )
    conn.commit()


def import_json(conn, path=None):
    file = Path(path or MOVIMIENTOS_JSON)
    rows = json.loads(file.read_text(encoding="utf-8"))
    return upsert_movements(conn, rows)


def ensure_ready(conn):
    count = conn.execute("SELECT COUNT(*) AS n FROM movimientos").fetchone()["n"]
    if count == 0 and MOVIMIENTOS_JSON.exists():
        import_json(conn, MOVIMIENTOS_JSON)
    apply_local(conn)
    return conn.execute("SELECT COUNT(*) AS n FROM movimientos").fetchone()["n"]


def _coverage_bounds(desde, hasta):
    if desde is None and hasta is None:
        return None
    if not desde or not hasta:
        raise ValueError("Indica las dos fechas de cobertura: --desde y --hasta.")
    try:
        first, last = date.fromisoformat(desde), date.fromisoformat(hasta)
    except ValueError as error:
        raise ValueError("La cobertura debe tener fechas válidas AAAA-MM-DD.") from error
    if first.isoformat() != desde or last.isoformat() != hasta or first > last:
        raise ValueError("La cobertura debe tener fechas AAAA-MM-DD en orden.")
    return desde, hasta


def import_file(conn, path, *, desde=None, hasta=None, cuenta=""):
    bounds = _coverage_bounds(desde, hasta)
    file = Path(path)
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    if not bounds and conn.execute("SELECT 1 FROM ficheros WHERE hash = ?", (digest,)).fetchone():
        return {"nombre": file.name, "estado": "ya cargado"}
    rows, ignored, lector = load_statement(file)
    breaks = balance_breaks(rows)
    if not rows and not bounds:
        return {"nombre": file.name, "estado": "vacio", "lector": lector}
    if breaks:
        return {
            "nombre": file.name,
            "estado": "saltos",
            "lector": lector,
            "movimientos": len(rows),
            "saltos": breaks,
        }
    dates = sorted(date.fromisoformat(row["fecha"]).isoformat() for row in rows)
    if bounds and dates and (dates[0] < desde or dates[-1] > hasta):
        raise ValueError("Hay movimientos fuera del intervalo de cobertura declarado.")
    occurrences = {}
    for row in rows:
        row["cuenta"] = cuenta
        row["extracto"] = digest
        if row.get('divisa', 'EUR').strip().upper() != 'EUR':
            raise ValueError('El panel solo admite extractos de cuentas en EUR.')
        row['divisa'] = 'EUR'
        key = movement_hash(row)
        row["ocurrencia"] = occurrences.get(key, 0)
        occurrences[key] = row["ocurrencia"] + 1
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT cuenta FROM ficheros WHERE hash = ?", (digest,)).fetchone()
        if existing:
            if bounds and existing['cuenta'] != cuenta:
                raise ValueError("La cuenta de un extracto ya cargado no se puede cambiar aquí.")
            inserted, updated = 0, 0
        else:
            inserted, updated = upsert_movements(conn, rows, commit=False)
            apply_local(conn, commit=False)
            conn.execute("INSERT INTO ficheros (hash, nombre, cargado, movimientos, cuenta) VALUES (?,?,?,?,?)",
                         (digest, file.name, _now(), len(rows), cuenta))
        if bounds:
            conn.execute("""INSERT INTO cobertura_extractos VALUES (?,?,?,?)
                          ON CONFLICT(fichero) DO UPDATE SET desde=excluded.desde, hasta=excluded.hasta""",
                         (digest, cuenta, *bounds))
    return {
        "nombre": file.name,
        "estado": "cobertura actualizada" if existing and bounds else "ya cargado" if existing else "cargado",
        "lector": lector,
        "movimientos": len(rows),
        "nuevos": inserted,
        "actualizados": updated,
        "ignoradas": len(ignored),
        "desde": dates[0] if dates else desde,
        "hasta": dates[-1] if dates else hasta,
    }


def scan_folder(conn, folder=None):
    root = Path(folder or ENTRADA)
    root.mkdir(parents=True, exist_ok=True)
    files = [
        path
        for path in sorted(root.iterdir())
        if path.is_file() and path.suffix.casefold() in {".pdf", ".xlsx", ".xlsm", ".csv"}
    ]
    results = []
    for path in files:
        try:
            results.append(import_file(conn, path))
        except (ValueError, OSError) as error:
            results.append({'nombre': path.name, 'estado': 'error', 'error': str(error)})
    return results


def _all(conn):
    rows = [dict(row) for row in conn.execute(
        """SELECT m.*, t.aporte AS traspaso_myinvestor FROM movimientos m
           LEFT JOIN traspasos_inversion t ON t.banco=m.hash AND t.estado='confirmado'
                AND m.fecha=t.fecha_banco AND m.importe=t.importe_banco
                AND m.divisa='EUR' AND m.direccion='gasto'
           WHERE NOT EXISTS
           (SELECT 1 FROM conciliacion c WHERE c.excluido=m.hash) ORDER BY fecha, hash""")]
    for row in rows:
        allowed = INGRESO if row['direccion'] == 'ingreso' else GASTO
        if row['categoria_manual'] in allowed:
            row.update(categoria=row['categoria_manual'], origen_clasificacion='manual', revisar=0, confianza=1.0)
        if row['traspaso_myinvestor']:
            row.update(categoria='ahorro_inversion', origen_clasificacion='traspaso', revisar=0, confianza=1.0)
    return rows


def set_manual_category(conn, digest, category):
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT direccion FROM movimientos WHERE hash=?", (digest,)).fetchone()
        if row is None:
            raise LookupError("Ese movimiento no existe.")
        if conn.execute("SELECT 1 FROM traspasos_inversion WHERE banco=? AND estado='confirmado'", (digest,)).fetchone():
            raise ValueError('Deshaz el enlace del traspaso antes de editar su categoría.')
        allowed = INGRESO if row['direccion'] == 'ingreso' else GASTO
        if category is not None and (not isinstance(category, str) or category not in allowed):
            raise ValueError("La categoría no es válida para la dirección de este movimiento.")
        conn.execute("UPDATE movimientos SET categoria_manual=? WHERE hash=?", (category, digest))


def report_rows(conn):
    return _all(conn)


def _candidate(a, b):
    feeds = {'pdf-texto', 'pdf-tabla', 'pdf-ocr', 'excel', 'csv'}
    sources = {a['lector'], b['lector']}
    cross_feed = ('enable-banking' in sources and bool(sources & feeds)) or (
        sources <= feeds and (a['extracto'] or a['fichero']) != (b['extracto'] or b['fichero']))
    return (a['hash'] != b['hash'] and cross_feed
            and a['fecha'] == b['fecha'] and a['importe'] == b['importe'] and a['divisa'] == b['divisa']
            and (not a['cuenta'] or not b['cuenta'] or a['cuenta'] == b['cuenta']))


def duplicate_candidates(conn, mes='todos'):
    rows = _selected(conn, mes)
    groups = {}
    decisions = {(r['primero'], r['segundo']): r['excluido'] for r in conn.execute('SELECT * FROM conciliacion')}
    locked = {digest for pair, excluded in decisions.items() if excluded is not None for digest in pair}

    def summary(row):
        return {k: row[k] for k in ('hash', 'fecha', 'concepto', 'detalle', 'importe', 'divisa', 'lector', 'fichero', 'saldo')}

    pairs = []
    for row in rows:
        if row['hash'] not in locked:
            groups.setdefault((row['fecha'], row['importe'], row['divisa']), []).append(row)
    for group in groups.values():
        for index, a in enumerate(group):
            for b in group[index + 1:]:
                key = tuple(sorted((a['hash'], b['hash'])))
                if key not in decisions and _candidate(a, b):
                    pairs.append({'primero': summary(a), 'segundo': summary(b)})
    history = []
    if decisions:
        records = {r['hash']: r for r in conn.execute('SELECT * FROM movimientos')}
        for (a, b), excluded in decisions.items():
            if mes == 'todos' or records[a]['fecha'].startswith(mes) or records[b]['fecha'].startswith(mes):
                history.append({'primero': summary(records[a]), 'segundo': summary(records[b]), 'excluido': excluded})
    return {'candidatos': pairs, 'decisiones': history}


def reconcile(conn, first, second, action, excluded=None):
    key = tuple(sorted((first, second)))
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if action == 'deshacer':
            conn.execute('DELETE FROM conciliacion WHERE primero=? AND segundo=?', key)
            return
        rows = conn.execute('SELECT * FROM movimientos WHERE hash IN (?,?)', key).fetchall()
        if len(rows) != 2 or not _candidate(*rows):
            raise ValueError('El par ya no es un candidato válido.')
        if conn.execute("""SELECT 1 FROM conciliacion WHERE excluido IS NOT NULL
                              AND (primero IN (?,?) OR segundo IN (?,?))""", (*key, *key)).fetchone():
            raise ValueError('Uno de los movimientos ya está conciliado. Deshaz primero la decisión anterior.')
        if action == 'duplicado' and excluded not in key:
            raise ValueError('Elige cuál de las dos filas quieres excluir.')
        if action == 'duplicado' and conn.execute("SELECT 1 FROM traspasos_inversion WHERE banco=? AND estado='confirmado'", (excluded,)).fetchone():
            raise ValueError('El apunte está enlazado a una aportación. Deshaz ese enlace antes de excluirlo.')
        if action not in {'distintos', 'duplicado'}:
            raise ValueError('Decisión no válida.')
        conn.execute("""INSERT INTO conciliacion VALUES (?,?,?,?)
                        ON CONFLICT(primero, segundo) DO UPDATE SET excluido=excluded.excluido, decidido=excluded.decidido""",
                     (*key, excluded if action == 'duplicado' else None, _now()))


def _selected(conn, mes):
    rows = _all(conn)
    if not mes or mes == "todos":
        return rows
    return [row for row in rows if row["fecha"].startswith(mes)]


def _bucket(categoria):
    if not categoria or categoria in SIN:
        return "sin_clasificar"
    if categoria in INGRESO_CATS:
        return "ingreso"
    if categoria == "ahorro_inversion":
        return "ahorro"
    if categoria == "bizum_personas":
        return "bizum_personas"
    if categoria in GASTO:
        return "consumo"
    return "sin_clasificar"


def _span(rows):
    if not rows:
        return None, None
    dates = sorted(row["fecha"] for row in rows)
    return dates[0], dates[-1]


def _months(conn, rows):
    windows = {}
    movements = {}
    for row in rows:
        movements.setdefault(row['fecha'][:7], []).append(row)
    for row in conn.execute("SELECT cuenta, desde, hasta FROM cobertura_extractos ORDER BY desde"):
        windows.setdefault(row['cuenta'], []).append((date.fromisoformat(row['desde']), date.fromisoformat(row['hasta'])))
    accounts = set(windows) | {row['cuenta'] for row in rows}
    month_keys = set(movements)
    for intervals in windows.values():
        for first, last in intervals:
            cursor = first.replace(day=1)
            while cursor <= last:
                month_keys.add(cursor.isoformat()[:7])
                end = cursor.replace(day=monthrange(cursor.year, cursor.month)[1])
                if end >= last:
                    break
                cursor = end + timedelta(days=1)
    if month_keys:
        cursor = date.fromisoformat(min(month_keys) + '-01')
        last_month = date.fromisoformat(max(month_keys) + '-01')
        while cursor < last_month:
            month_keys.add(cursor.isoformat()[:7])
            cursor = cursor.replace(day=monthrange(cursor.year, cursor.month)[1]) + timedelta(days=1)
    found = []
    for month in sorted(month_keys):
        year, number = map(int, month.split('-'))
        start = date(year, number, 1)
        end = date(year, number, monthrange(year, number)[1])
        complete = set()
        has_interval = False
        for account, intervals in windows.items():
            cursor = start.toordinal()
            for first, last in intervals:
                if first > end:
                    break
                if last < start:
                    continue
                has_interval = True
                if first.toordinal() > cursor:
                    break
                cursor = max(cursor, last.toordinal() + 1)
                if cursor > end.toordinal():
                    complete.add(account)
                    break
        status = 'completa' if complete == accounts else 'parcial' if has_interval else 'desconocida'
        seen_first, seen_last = _span(movements.get(month, []))
        found.append(
            {
                "mes": month,
                "completo": status == 'completa',
                "cobertura": status,
                "desde": seen_first,
                "hasta": seen_last,
                "movimientos": len(movements.get(month, [])),
            }
        )
    return found


def mes_largo(month):
    year, number = month.split("-")
    return f"{MESES[int(number) - 1]} de {year}"


def meses(conn):
    rows = _all(conn)
    first, last = _span(rows)
    sources = [dict(row) for row in conn.execute("""
        SELECT f.nombre, c.desde, c.hasta FROM ficheros f
        LEFT JOIN cobertura_extractos c ON f.hash=c.fichero ORDER BY c.desde DESC, f.nombre
    """)]
    return {"meses": _months(conn, rows), "desde": first, "hasta": last, "extractos": sources}


def _totals(rows):
    boxes = {
        "ingreso": Decimal("0"),
        "consumo": Decimal("0"),
        "ahorro": Decimal("0"),
        "bizum_personas": Decimal("0"),
        "sin_clasificar": Decimal("0"),
        "resultado": Decimal("0"),
    }
    pending = 0
    for row in rows:
        amount = Decimal(row["importe"])
        boxes["resultado"] += amount
        kind = _bucket(row.get("categoria"))
        if kind == "consumo":
            boxes["consumo"] += amount
        elif kind == "ingreso":
            boxes["ingreso"] += amount
        else:
            boxes[kind] += amount
        if kind == "sin_clasificar":
            pending += 1
    return boxes, pending


def evolucion(conn):
    rows = _all(conn)
    series = []
    for month in _months(conn, rows):
        chosen = [row for row in rows if row["fecha"].startswith(month["mes"])]
        boxes, _pending = _totals(chosen)
        series.append(
            {
                "mes": month["mes"],
                "completo": month["completo"],
                "cobertura": month["cobertura"],
                "sin_datos": not month["movimientos"] and not month["completo"],
                "ingresos": money(boxes["ingreso"]),
                "consumo": money(-boxes["consumo"]),
                "ahorro": money(-boxes["ahorro"]),
                "desde": month["desde"],
                "hasta": month["hasta"],
            }
        )
    return series


def _previous(month):
    year, number = map(int, month.split("-"))
    if number == 1:
        return f"{year - 1}-12"
    return f"{year}-{number - 1:02d}"


def _percent(current, previous):
    if previous == 0:
        return None
    change = (current - previous) / abs(previous) * Decimal("100")
    return format(change.quantize(Decimal("0.1")), "f")


def kpis(conn, mes="todos"):
    rows = _all(conn)
    chosen = rows if not mes or mes == "todos" else [row for row in rows if row["fecha"].startswith(mes)]
    boxes, pending = _totals(chosen)
    info = _months(conn, rows)
    by_month = {item["mes"]: item for item in info}
    complete_months = [item["mes"] for item in info if item["completo"]]
    medio = None
    if complete_months and (not mes or mes == "todos"):
        spent = []
        for month in complete_months:
            month_rows = [row for row in rows if row["fecha"].startswith(month)]
            month_boxes, _ = _totals(month_rows)
            spent.append(-month_boxes["consumo"])
        if spent:
            medio = money(sum(spent) / len(spent))
    variacion = None
    if mes and mes != "todos" and mes in by_month:
        previous = _previous(mes)
        if previous in by_month and by_month[mes]["completo"] and by_month[previous]["completo"]:
            current_spend = -boxes["consumo"]
            previous_rows = [row for row in rows if row["fecha"].startswith(previous)]
            previous_spend = -_totals(previous_rows)[0]["consumo"]
            variacion = _percent(current_spend, previous_spend)
    status = by_month.get(mes, {}).get('cobertura', 'desconocida')
    if not mes or mes == 'todos':
        covered = sum((item['cobertura'] != 'desconocida' for item in info))
        status = ('completa' if info and len(complete_months) == len(info)
                  else 'parcial' if covered else 'desconocida')
    start, end = _span(chosen)
    traspasos = [Decimal(row['importe']) for row in chosen if row['traspaso_myinvestor']]
    return {
        "mes": mes or "todos",
        "completo": status == 'completa',
        "cobertura": status,
        "desde": start,
        "hasta": end,
        "ingresos": money(boxes["ingreso"]),
        "consumo": money(-boxes["consumo"]),
        "ahorro": money(-boxes["ahorro"]),
        "traspasos_myinvestor": money(-sum(traspasos, Decimal('0'))),
        "traspasos_myinvestor_n": len(traspasos),
        "bizum_personas": money(-boxes["bizum_personas"]),
        "sin_clasificar": money(boxes["sin_clasificar"]),
        "sin_clasificar_n": pending,
        "resultado": money(boxes["resultado"]),
        "variacion_consumo": variacion,
        "consumo_medio_meses_completos": medio,
        "movimientos": len(chosen),
        "revisar_n": sum(bool(row["revisar"]) for row in chosen),
    }


def categorias(conn, mes="todos"):
    chosen = _selected(conn, mes)
    grouped = {}
    for row in chosen:
        key = row.get("categoria") or "sin_clasificar"
        bucket = grouped.setdefault(key, {"importe": Decimal("0"), "movimientos": 0})
        bucket["importe"] += Decimal(row["importe"])
        bucket["movimientos"] += 1
    consumo = []
    ingresos = []
    aparte = []
    for key, bucket in grouped.items():
        item = {
            "categoria": key,
            "etiqueta": etiquetar(key),
            "importe": money(bucket["importe"]),
            "movimientos": bucket["movimientos"],
        }
        kind = _bucket(key)
        if kind == "consumo":
            consumo.append(item)
        elif kind == "ingreso":
            ingresos.append(item)
        else:
            aparte.append(item)
    consumo.sort(key=lambda item: abs(Decimal(item["importe"])), reverse=True)
    ingresos.sort(key=lambda item: abs(Decimal(item["importe"])), reverse=True)
    aparte.sort(key=lambda item: abs(Decimal(item["importe"])), reverse=True)
    return {"consumo": consumo, "ingresos": ingresos, "aparte": aparte}


def list_movimientos(conn, mes="todos", categoria=None, limit=50):
    chosen = _selected(conn, mes)
    if categoria:
        chosen = [row for row in chosen if (row.get("categoria") or "sin_clasificar") == categoria]
    chosen.sort(key=lambda row: abs(Decimal(row["importe"])), reverse=True)
    output = []
    for row in chosen[:limit]:
        output.append(
            {
                "fecha": row["fecha"],
                "concepto": row["concepto"],
                "detalle": row.get("detalle") or "",
                "importe": row["importe"],
                "categoria": row.get("categoria") or "sin_clasificar",
                "etiqueta": etiquetar(row.get("categoria")),
                "origen": row.get("origen_clasificacion") or "pendiente",
                "revisar": bool(row.get("revisar")),
                "direccion": row["direccion"],
            }
        )
    return output


def _search_text(value):
    return "".join(
        char for char in unicodedata.normalize("NFD", str(value).casefold())
        if not unicodedata.combining(char)
    )


def query_movimientos(conn, mes="todos", *, categoria="", texto="", direccion="",
                      origen="", revisar=False, orden="fecha", sentido="desc",
                      limit=50, offset=0):
    """Filtra y ordena el periodo completo antes de paginar; suma con Decimal."""
    period = _selected(conn, mes)
    needle = _search_text(texto.strip())
    chosen = []
    for row in period:
        category = row.get("categoria") or "sin_clasificar"
        if categoria and category != categoria:
            continue
        if direccion and row["direccion"] != direccion:
            continue
        if origen and row["origen_clasificacion"] != origen:
            continue
        if revisar and not row["revisar"]:
            continue
        if needle and needle not in _search_text(
            f"{row['concepto']} {row['detalle']} {etiquetar(category)}"
        ):
            continue
        chosen.append({
            "hash": row["hash"], "fecha": row["fecha"], "concepto": row["concepto"],
            "detalle": row["detalle"], "importe": row["importe"],
            "categoria": category, "etiqueta": etiquetar(category),
            "origen": row["origen_clasificacion"], "revisar": bool(row["revisar"]),
            "direccion": row["direccion"], "categoria_manual": row["categoria_manual"],
            "traspaso_myinvestor": bool(row['traspaso_myinvestor']),
        })

    def sort_key(row):
        value = Decimal(row[orden]) if orden == "importe" else _search_text(row[orden])
        return value, row["fecha"], row["hash"]

    chosen.sort(key=sort_key, reverse=sentido == "desc")
    total = len(chosen)
    amounts = [Decimal(row["importe"]) for row in chosen]
    ingresos = sum((n for n in amounts if n > 0), Decimal("0"))
    salidas = -sum((n for n in amounts if n < 0), Decimal("0"))
    if limit is not None:
        offset = min(offset, ((total - 1) // limit) * limit) if total else 0
        page = chosen[offset:offset + limit]
    else:
        offset, page = 0, chosen
    return {
        "movimientos": page, "total": total, "total_periodo": len(period),
        "limit": limit, "offset": offset,
        "resumen": {"ingresos": money(ingresos), "salidas": money(salidas),
                    "neto": money(ingresos - salidas)},
    }


def insights(conn, mes="todos"):
    rows = _all(conn)
    if not rows:
        return {"frases": ["Todavía no hay movimientos en la base de datos."]}
    info = _months(conn, rows)
    by_month = {item["mes"]: item for item in info}
    chosen = rows if not mes or mes == "todos" else [row for row in rows if row["fecha"].startswith(mes)]
    frases = []
    if mes and mes != "todos" and mes in by_month and not by_month[mes]["completo"]:
        frases.append(
            f"{mes_largo(mes).capitalize()}: cobertura {by_month[mes]['cobertura']}."
        )
    cats = categorias(conn, mes)["consumo"]
    if cats:
        top = cats[0]
        total = sum((abs(Decimal(item["importe"])) for item in cats), Decimal("0"))
        share = (abs(Decimal(top["importe"])) / total * Decimal("100")) if total else Decimal("0")
        frases.append(
            f"La mayor parte del consumo está en {top['etiqueta']}: "
            f"{euros(top['importe'])}, un {share.quantize(Decimal('1'))} % del consumo clasificado."
        )
    boxes, pending = _totals(chosen)
    if pending:
        frases.append(
            f"{pending} movimientos siguen sin categoría y suman {euros(boxes['sin_clasificar'])}. "
            "No entran en el consumo."
        )
    if boxes["bizum_personas"] != 0:
        frases.append(
            f"Los Bizum a personas suman {euros(boxes['bizum_personas'])} y quedan fuera del consumo."
        )
    if boxes["ahorro"] != 0:
        frases.append(
            f"El ahorro y la inversión mueven {euros(boxes['ahorro'])}. Tampoco entran en el consumo."
        )
    if not mes or mes == "todos":
        series = evolucion(conn)
        complete = [item for item in series if item["completo"]]
        if len(complete) >= 2:
            top_month = max(complete, key=lambda item: Decimal(item["consumo"]))
            frases.append(f"Entre los meses completos, el de más consumo es {mes_largo(top_month['mes'])}.")
        if complete:
            average = sum((Decimal(item["consumo"]) for item in complete), Decimal("0")) / len(complete)
            frases.append(
                f"La media de consumo de los {len(complete)} meses completos es {euros(-average)}."
            )
    elif mes in by_month:
        previous = _previous(mes)
        if previous in by_month and by_month[mes]["completo"] and by_month[previous]["completo"]:
            current = Decimal(kpis(conn, mes)["consumo"])
            before = Decimal(kpis(conn, previous)["consumo"])
            change = _percent(current, before)
            if change is not None:
                if Decimal(change) == 0:
                    frases.append(f"El consumo de {mes_largo(mes)} se mantiene respecto a {mes_largo(previous)}.")
                else:
                    direction = "sube" if Decimal(change) > 0 else "baja"
                    points = format(abs(Decimal(change)), "f").replace(".", ",")
                    frases.append(
                        f"El consumo de {mes_largo(mes)} {direction} un {points} % respecto a {mes_largo(previous)}."
                    )
    if chosen:
        biggest = max(chosen, key=lambda row: abs(Decimal(row["importe"])))
        frases.append(
            f"El movimiento de mayor importe es {euros(biggest['importe'])} "
            f"el {biggest['fecha']} ({biggest['concepto']})."
        )
    return {"frases": frases[:7]}


def estado(conn):
    rows = _all(conn)
    first, last = _span(rows)
    return {
        "movimientos": len(rows),
        "pendientes": sum(row['origen_clasificacion'] == 'pendiente' for row in rows),
        "desde": first,
        "hasta": last,
        "base": "data/gastos.sqlite",
    }

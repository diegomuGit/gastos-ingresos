"""SQLite local. El panel lee de aquí; el HTML no lleva cifras del extracto."""

import csv
import hashlib
import json
import os
import re
import sqlite3
from calendar import monthrange
from datetime import datetime, timezone
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
"""


def connect(path=None):
    chosen = Path(path or os.environ.get("BBVA_GASTOS_DB") or DB_PATH)
    chosen.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(chosen)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
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
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_movements(conn, rows):
    now = _now()
    inserted = 0
    updated = 0
    for row in rows:
        if not row.get("fecha"):
            continue
        digest = movement_hash(row)
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
            digest,
        )
        if found:
            conn.execute(
                """UPDATE movimientos
                   SET fecha=?, fecha_valor=?, concepto=?, detalle=?, importe=?, saldo=?,
                       divisa=?, direccion=?, lector=?, fichero=?, externo=?, actualizado=?
                   WHERE hash=?""",
                values,
            )
            updated += 1
        else:
            conn.execute(
                """INSERT INTO movimientos (
                       fecha, fecha_valor, concepto, detalle, importe, saldo, divisa,
                       direccion, lector, fichero, externo, actualizado, hash,
                       origen_clasificacion, revisar
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'pendiente',1)""",
                values,
            )
            inserted += 1
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


def apply_local(conn, corrections=None):
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
    conn.commit()


def save_labels(conn, rows):
    for row in rows:
        origen = row.get("origen")
        if origen not in {"jev", "regla"}:
            continue
        digest = movement_hash(row)
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


def import_file(conn, path):
    file = Path(path)
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    if conn.execute("SELECT 1 FROM ficheros WHERE hash = ?", (digest,)).fetchone():
        return {"nombre": file.name, "estado": "ya cargado"}
    rows, ignored, lector = load_statement(file)
    breaks = balance_breaks(rows)
    if not rows:
        return {"nombre": file.name, "estado": "vacio", "lector": lector}
    if breaks:
        return {
            "nombre": file.name,
            "estado": "saltos",
            "lector": lector,
            "movimientos": len(rows),
            "saltos": breaks,
        }
    inserted, updated = upsert_movements(conn, rows)
    apply_local(conn)
    conn.execute(
        "INSERT INTO ficheros (hash, nombre, cargado, movimientos) VALUES (?,?,?,?)",
        (digest, file.name, _now(), len(rows)),
    )
    conn.commit()
    dates = sorted(row["fecha"] for row in rows)
    return {
        "nombre": file.name,
        "estado": "cargado",
        "lector": lector,
        "movimientos": len(rows),
        "nuevos": inserted,
        "actualizados": updated,
        "ignoradas": len(ignored),
        "desde": dates[0],
        "hasta": dates[-1],
    }


def scan_folder(conn, folder=None):
    root = Path(folder or ENTRADA)
    root.mkdir(parents=True, exist_ok=True)
    files = [
        path
        for path in sorted(root.iterdir())
        if path.is_file() and path.suffix.casefold() in {".pdf", ".xlsx", ".xlsm", ".csv"}
    ]
    return [import_file(conn, path) for path in files]


def _all(conn):
    return [dict(row) for row in conn.execute("SELECT * FROM movimientos ORDER BY fecha, hash")]


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


def _complete(month, first, last):
    if not first or not last:
        return False
    year, number = map(int, month.split("-"))
    start = f"{month}-01"
    end = f"{month}-{monthrange(year, number)[1]:02d}"
    return first <= start and last >= end


def _months(rows, first, last):
    found = []
    for month in sorted({row["fecha"][:7] for row in rows}):
        in_month = [row for row in rows if row["fecha"].startswith(month)]
        start, end = _span(in_month)
        found.append(
            {
                "mes": month,
                "completo": _complete(month, first, last),
                "desde": start,
                "hasta": end,
            }
        )
    return found


def mes_largo(month):
    year, number = month.split("-")
    return f"{MESES[int(number) - 1]} de {year}"


def meses(conn):
    rows = _all(conn)
    first, last = _span(rows)
    return {"meses": _months(rows, first, last), "desde": first, "hasta": last}


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
    first, last = _span(rows)
    series = []
    for month in _months(rows, first, last):
        chosen = [row for row in rows if row["fecha"].startswith(month["mes"])]
        boxes, _pending = _totals(chosen)
        series.append(
            {
                "mes": month["mes"],
                "completo": month["completo"],
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
    first, last = _span(rows)
    chosen = rows if not mes or mes == "todos" else [row for row in rows if row["fecha"].startswith(mes)]
    boxes, pending = _totals(chosen)
    info = _months(rows, first, last)
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
        if previous in by_month:
            current_spend = -boxes["consumo"]
            previous_rows = [row for row in rows if row["fecha"].startswith(previous)]
            previous_spend = -_totals(previous_rows)[0]["consumo"]
            variacion = _percent(current_spend, previous_spend)
    period_complete = bool(chosen) and all(
        by_month[row["fecha"][:7]]["completo"] for row in chosen if row["fecha"][:7] in by_month
    )
    start, end = _span(chosen)
    return {
        "mes": mes or "todos",
        "completo": period_complete if mes and mes != "todos" else bool(complete_months) and len(complete_months) == len(info) and bool(info),
        "desde": start,
        "hasta": end,
        "ingresos": money(boxes["ingreso"]),
        "consumo": money(-boxes["consumo"]),
        "ahorro": money(-boxes["ahorro"]),
        "bizum_personas": money(-boxes["bizum_personas"]),
        "sin_clasificar": money(boxes["sin_clasificar"]),
        "sin_clasificar_n": pending,
        "resultado": money(boxes["resultado"]),
        "variacion_consumo": variacion,
        "consumo_medio_meses_completos": medio,
        "movimientos": len(chosen),
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


def insights(conn, mes="todos"):
    rows = _all(conn)
    if not rows:
        return {"frases": ["Todavía no hay movimientos en la base de datos."]}
    first, last = _span(rows)
    info = _months(rows, first, last)
    by_month = {item["mes"]: item for item in info}
    chosen = rows if not mes or mes == "todos" else [row for row in rows if row["fecha"].startswith(mes)]
    frases = []
    if mes and mes != "todos" and mes in by_month and not by_month[mes]["completo"]:
        item = by_month[mes]
        frases.append(
            f"{mes_largo(mes).capitalize()} no está completo: hay movimientos del {item['desde']} al {item['hasta']}."
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
        if len(series) >= 2:
            top_month = max(series, key=lambda item: Decimal(item["consumo"]))
            frases.append(f"El mes con más consumo es {mes_largo(top_month['mes'])}.")
        complete = [item for item in series if item["completo"]]
        if complete:
            average = sum((Decimal(item["consumo"]) for item in complete), Decimal("0")) / len(complete)
            frases.append(
                f"La media de consumo de los {len(complete)} meses completos es {euros(-average)}."
            )
    elif mes in by_month:
        previous = _previous(mes)
        if previous in by_month:
            current = Decimal(kpis(conn, mes)["consumo"])
            before = Decimal(kpis(conn, previous)["consumo"])
            change = _percent(current, before)
            if change is not None:
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
    row = conn.execute(
        "SELECT COUNT(*) AS n, MIN(fecha) AS desde, MAX(fecha) AS hasta FROM movimientos"
    ).fetchone()
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM movimientos WHERE origen_clasificacion = 'pendiente'"
    ).fetchone()["n"]
    return {
        "movimientos": row["n"],
        "pendientes": pending,
        "desde": row["desde"],
        "hasta": row["hasta"],
        "base": "data/gastos.sqlite",
    }

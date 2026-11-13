import csv
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

MAIN = re.compile(
    r"^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+"
    r"(-?\d{1,3}(?:\.\d{3})*,\d{2})\s*€\s+"
    r"(-?\d{1,3}(?:\.\d{3})*,\d{2})\s+EUR$"
)
DETAIL = re.compile(r"^F\.Valor:\s+(\d{2}/\d{2}/\d{4})\s*(.*)$")
SKIP = re.compile(r"^(Últimos movimientos|Fecha Concepto Importe Saldo|\d+/\d+)$")


def parse_amount(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip()
    if not text or text in {"-", "—"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("€", "").replace("EUR", "").replace("\u00a0", "").replace(" ", "")
    if text.startswith("-"):
        negative = True
        text = text[1:]
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(text)
    except Exception:
        return None
    return -amount if negative else amount


def parse_date(text):
    day, month, year = text.split("/")
    return f"{year}-{month}-{day}"


def parse_bbva_text(text):
    rows = []
    pending = None
    ignored = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line or SKIP.match(line):
            continue
        main = MAIN.match(line)
        if main:
            if pending:
                rows.append(pending)
            pending = {
                "fecha": parse_date(main.group(1)),
                "fecha_valor": "",
                "concepto": main.group(2).strip(),
                "detalle": "",
                "importe": format(parse_amount(main.group(3)), "f"),
                "saldo": format(parse_amount(main.group(4)), "f"),
                "divisa": "EUR",
            }
            pending["direccion"] = "ingreso" if Decimal(pending["importe"]) >= 0 else "gasto"
            continue
        detail = DETAIL.match(line)
        if detail and pending:
            pending["fecha_valor"] = parse_date(detail.group(1))
            pending["detalle"] = detail.group(2).strip()
            rows.append(pending)
            pending = None
            continue
        if rows and not pending and rows[-1]["detalle"].casefold() == "euros":
            rows[-1]["concepto"] = f"{rows[-1]['concepto']} euros"
            rows[-1]["detalle"] = line
            continue
        ignored.append(line)
    if pending:
        rows.append(pending)
    return rows, ignored


ALIASES = (
    ("fecha", {"fecha", "f operacion", "fecha operacion", "fecha oper", "fecha del movimiento", "date"}),
    ("fecha_valor", {"f valor", "fecha valor", "fecha de valor", "value date"}),
    ("concepto", {"concepto", "movimiento", "descripcion", "concepto movimiento"}),
    ("detalle", {"detalle", "observaciones", "mas datos", "descripcion ampliada"}),
    ("importe", {"importe", "importe eur", "amount", "cantidad"}),
    ("cargo", {"cargo", "cargos", "debe"}),
    ("abono", {"abono", "abonos", "haber"}),
    ("saldo", {"saldo", "saldo disponible", "disponible", "balance"}),
    ("divisa", {"divisa", "moneda", "currency"}),
)
SKIP_CONCEPT = re.compile(r"^(saldo inicial|saldo anterior|saldo final)$", re.IGNORECASE)


def norm_header(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    return " ".join(text.split())


def map_header(row):
    mapping = {}
    for index, cell in enumerate(row or []):
        key = norm_header(cell)
        if not key:
            continue
        for field, names in ALIASES:
            if key in names and field not in mapping:
                mapping[field] = index
                break
    if "fecha" not in mapping or "concepto" not in mapping:
        return None
    if not ({"importe", "cargo", "abono"} & set(mapping)):
        return None
    return mapping


def parse_date_value(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if re.fullmatch(r"\d{2}/\d{2}/\d{4}", text):
        return parse_date(text)
    if re.fullmatch(r"\d{2}-\d{2}-\d{4}", text):
        day, month, year = text.split("-")
        return f"{year}-{month}-{day}"
    if len(text) >= 10 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text[:10]):
        return text[:10]
    return ""


def _cell(cells, mapping, name):
    index = mapping.get(name)
    if index is None or index >= len(cells):
        return None
    return cells[index]


def row_from_cells(cells, mapping):
    if not cells or not any(cell not in (None, "") for cell in cells):
        return None
    if map_header(cells):
        return None
    fecha = parse_date_value(_cell(cells, mapping, "fecha"))
    concepto = str(_cell(cells, mapping, "concepto") or "").strip()
    if not fecha or not concepto or SKIP_CONCEPT.match(concepto):
        return None
    importe = parse_amount(_cell(cells, mapping, "importe"))
    if importe is None:
        abono = parse_amount(_cell(cells, mapping, "abono")) or Decimal("0")
        cargo = parse_amount(_cell(cells, mapping, "cargo")) or Decimal("0")
        if abono == 0 and cargo == 0:
            return None
        importe = abono - abs(cargo)
    saldo = parse_amount(_cell(cells, mapping, "saldo"))
    fecha_valor = parse_date_value(_cell(cells, mapping, "fecha_valor"))
    divisa = str(_cell(cells, mapping, "divisa") or "EUR").strip() or "EUR"
    detalle = str(_cell(cells, mapping, "detalle") or "").strip()
    return {
        "fecha": fecha,
        "fecha_valor": fecha_valor,
        "concepto": concepto,
        "detalle": detalle,
        "importe": format(importe, "f"),
        "saldo": format(saldo, "f") if saldo is not None else "",
        "divisa": divisa.replace("€", "EUR"),
        "direccion": "ingreso" if importe >= 0 else "gasto",
    }


def rows_from_matrix(matrix):
    best = []
    for index, raw in enumerate(matrix[:40]):
        mapping = map_header(raw)
        if not mapping:
            continue
        parsed = []
        for cells in matrix[index + 1 :]:
            item = row_from_cells(list(cells or []), mapping)
            if item:
                parsed.append(item)
        if len(parsed) > len(best):
            best = parsed
    return best


def parse_pdf(path):
    import pdfplumber

    parts = []
    tables = []
    with pdfplumber.open(path) as doc:
        if not doc.pages:
            raise ValueError("El PDF no tiene páginas.")
        for page in doc.pages:
            parts.append(page.extract_text() or "")
            for table in page.extract_tables() or []:
                tables.extend(table)
    rows, ignored = parse_bbva_text("\n".join(parts))
    if rows:
        return rows, ignored, "pdf-texto"
    tabular = rows_from_matrix(tables)
    if tabular:
        return tabular, ignored, "pdf-tabla"
    scanned = _ocr_pdf(path)
    if scanned:
        return scanned, [], "pdf-ocr"
    raise ValueError(
        "Este PDF no tiene texto ni una tabla de movimientos. "
        "Si es una foto o un escaneo, instala Tesseract (idioma spa) y vuelve a cargarlo. "
        "Si puedes, descarga el Excel desde bbva.es: se lee mejor."
    )


def _ocr_pdf(path):
    try:
        import pytesseract
    except ImportError:
        return []
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return []
    import pdfplumber

    chunks = []
    with pdfplumber.open(path) as doc:
        for page in doc.pages:
            image = page.to_image(resolution=200).original
            chunks.append(pytesseract.image_to_string(image, lang="spa+eng"))
    rows, _ignored = parse_bbva_text("\n".join(chunks))
    return rows


def parse_xlsx(path):
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    best = []
    for sheet in book.worksheets:
        matrix = [list(row) for row in sheet.iter_rows(values_only=True)]
        parsed = rows_from_matrix(matrix)
        if len(parsed) > len(best):
            best = parsed
    book.close()
    if not best:
        raise ValueError(
            "No encuentro movimientos en el Excel. "
            "Hace falta una fila con fecha, concepto e importe, o cargo y abono."
        )
    return best, [], "excel"


def _read_csv_matrix(path):
    last_error = None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            raw = Path(path).read_text(encoding=encoding)
        except UnicodeError as error:
            last_error = error
            continue
        sample = "\n".join(line for line in raw.splitlines() if line.strip())
        counts = {delimiter: sample.count(delimiter) for delimiter in (";", ",", "\t")}
        delimiter = max(counts, key=counts.get)
        if counts[delimiter] == 0:
            delimiter = ","
        return list(csv.reader(raw.splitlines(), delimiter=delimiter))
    raise ValueError(f"No pude leer el CSV: {last_error}")


def parse_csv(path):
    rows = rows_from_matrix(_read_csv_matrix(path))
    if not rows:
        raise ValueError("No encuentro movimientos en el CSV.")
    return rows, [], "csv"


def load_statement(path):
    file = Path(path)
    suffix = file.suffix.casefold()
    if suffix == ".pdf":
        rows, ignored, lector = parse_pdf(file)
    elif suffix in {".xlsx", ".xlsm"}:
        rows, ignored, lector = parse_xlsx(file)
    elif suffix == ".csv":
        rows, ignored, lector = parse_csv(file)
    else:
        raise ValueError(f"Formato no reconocido: {file.suffix or '(sin extensión)'}. Usa PDF, Excel o CSV.")
    for row in rows:
        row["lector"] = lector
        row["fichero"] = file.name
    return rows, ignored, lector


def balance_breaks(rows):
    breaks = 0
    for newer, older in zip(rows, rows[1:]):
        if not newer.get("saldo") or not older.get("saldo"):
            continue
        expected = Decimal(older["saldo"]) + Decimal(newer["importe"])
        if expected != Decimal(newer["saldo"]):
            breaks += 1
    return breaks

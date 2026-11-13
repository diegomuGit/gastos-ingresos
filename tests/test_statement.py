from datetime import datetime
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from bbva_gastos.categories import match_rule
from bbva_gastos.report import summarize
from bbva_gastos.statement import balance_breaks, load_statement, parse_bbva_text

SAMPLE = """
Últimos movimientos
Fecha Concepto Importe Saldo
13/11/2026 Bizum 100,00 € 1.100,00 EUR
F.Valor: 13/11/2026 Recibido: sin concepto
13/11/2026 Factory fit madrid es -2,50 € 1.000,00 EUR
F.Valor: 13/11/2026 Pago con tarjeta
04/11/2026 Transferencia realizada -50,00 € 1.002,50 EUR
F.Valor: 04/11/2026 myinvestor
19/06/2026 Abono de nómina 200,00 € 1.052,50 EUR
F.Valor: 20/06/2026 Matr
1/2
"""


def test_parse_bbva_pairs_and_signs():
    rows, ignored = parse_bbva_text(SAMPLE)
    assert ignored == []
    assert [row["concepto"] for row in rows] == [
        "Bizum",
        "Factory fit madrid es",
        "Transferencia realizada",
        "Abono de nómina",
    ]
    assert rows[0]["importe"] == "100.00"
    assert rows[0]["direccion"] == "ingreso"
    assert rows[1]["importe"] == "-2.50"
    assert rows[1]["detalle"] == "Pago con tarjeta"
    assert rows[2]["importe"] == "-50.00"
    assert rows[0]["fecha"] == "2026-11-13"
    assert balance_breaks(rows) == 0


def test_wrapped_transfer_keeps_the_detail():
    text = """
30/04/2026 Abono por transferencia a su favor recibida en 1.234,56 € 2.000,00 EUR
F.Valor: 30/04/2026 euros
Transferencia recibida
30/04/2026 Adeudo ejemplo s.a. -10,00 € 765,44 EUR
F.Valor: 30/04/2026 Adeudo nº 1
"""
    rows, ignored = parse_bbva_text(text)
    assert ignored == []
    assert rows[0]["importe"] == "1234.56"
    assert rows[0]["detalle"] == "Transferencia recibida"
    assert rows[0]["concepto"].endswith("euros")


def test_rules_catch_payroll_and_broker():
    rows, _ = parse_bbva_text(SAMPLE)
    assert match_rule(rows[2]) == "ahorro_inversion"
    assert match_rule(rows[3]) == "nomina"
    assert match_rule(rows[0]) == "bizum_recibido"
    assert match_rule(rows[1]) == "deporte"


def test_excel_finds_header_below_a_title_and_csv_uses_cargo_abono():
    folder = Path(__file__).resolve().parent / "_tmp"
    folder.mkdir(exist_ok=True)
    book = Workbook()
    cover = book.active
    cover.title = "Portada"
    cover.append(["Extracto"])
    sheet = book.create_sheet("Movimientos")
    sheet.append(["BBVA", None, None, None, None])
    sheet.append([])
    sheet.append(["F. Operación", "F. Valor", "Movimiento", "Importe (€)", "Disponible"])
    sheet.append([datetime(2026, 11, 13), datetime(2026, 11, 13), "Bizum", 100, 1100])
    sheet.append([datetime(2026, 11, 12), datetime(2026, 11, 12), "Saldo inicial", None, 1000])
    sheet.append([datetime(2026, 11, 12), datetime(2026, 11, 12), "Cafe", -2.55, 2987.43])
    excel = folder / "bbva.xlsx"
    book.save(excel)
    rows, ignored, lector = load_statement(excel)
    assert lector == "excel"
    assert ignored == []
    assert [(row["concepto"], row["importe"]) for row in rows] == [("Bizum", "100"), ("Cafe", "-2.55")]
    assert rows[0]["fichero"] == "bbva.xlsx"

    csv_path = folder / "bbva.csv"
    csv_path.write_text(
        "Cuenta de ejemplo\n"
        "Fecha;Concepto;Cargo;Abono;Saldo\n"
        "13/11/2026;Bizum;;100,00;1.100,00\n"
        "12/11/2026;Cafe;2,55;;2.987,43\n",
        encoding="cp1252",
    )
    csv_rows, _, csv_lector = load_statement(csv_path)
    assert csv_lector == "csv"
    assert [row["importe"] for row in csv_rows] == ["100.00", "-2.55"]


def test_summary_separates_savings_from_spending():
    rows, _ = parse_bbva_text(SAMPLE)
    classified = []
    for row, category in zip(rows, ["bizum_recibido", "deporte", "ahorro_inversion", "nomina"]):
        classified.append({**row, "categoria": category, "revisar": False})
    summary = summarize(classified)
    november = summary["meses"][1]
    assert november["mes"] == "2026-11"
    assert Decimal(november["ingresos"]) == Decimal("100.00")
    assert Decimal(november["gastos"]) == Decimal("-2.50")
    assert Decimal(november["ahorro_inversion"]) == Decimal("-50.00")

import json
import os
import re
import threading
import urllib.request
from decimal import Decimal
from pathlib import Path

from bbva_gastos.categories import match_rule
from bbva_gastos.db import (
    apply_local,
    connect,
    evolucion,
    import_file,
    kpis,
    meses,
    save_labels,
    upsert_movements,
)
from bbva_gastos.report import summarize
from bbva_gastos.server import make_server

TMP = Path(__file__).resolve().parent / "_tmp"
WEB = Path(__file__).resolve().parents[1] / "bbva_gastos" / "web" / "index.html"


def _db(name):
    TMP.mkdir(exist_ok=True)
    path = TMP / name
    if path.exists():
        path.unlink()
    return connect(path)


def _row(**extra):
    base = {
        "fecha": "2026-01-10",
        "fecha_valor": "2026-01-10",
        "concepto": "Algo",
        "detalle": "",
        "importe": "-1.00",
        "saldo": "1.00",
        "divisa": "EUR",
        "direccion": "gasto",
        "lector": "test",
        "fichero": "test",
    }
    base.update(extra)
    return base


def test_rules_send_amazon_to_shops_and_rai_to_people():
    assert match_rule(_row(concepto="Bizum", detalle="Enviado: amazon", importe="-10.00")) == "compras"
    assert match_rule(_row(concepto="Bizum", detalle="Enviado: una persona", importe="-990.00")) == "bizum_personas"
    assert match_rule(_row(concepto="Bizum", detalle="Compra: pedido 1", importe="-4.00")) is None
    assert match_rule(_row(concepto="Zala ndo pay", detalle="Pago con tarjeta")) == "compras"
    assert match_rule(_row(concepto="Hm es0137", detalle="Pago con tarjeta")) == "compras"


def test_evolucion_and_kpis_come_from_the_database():
    conn = _db("kpis.sqlite")
    upsert_movements(
        conn,
        [
            _row(concepto="Abono de nómina", detalle="Matr", importe="100.00", saldo="100.00", direccion="ingreso"),
            _row(fecha="2026-01-11", concepto="Transferencia realizada", detalle="myinvestor", importe="-50.00", saldo="50.00"),
            _row(fecha="2026-01-12", concepto="Bizum", detalle="Enviado: una persona", importe="-20.00", saldo="30.00"),
            _row(fecha="2026-01-13", concepto="Delikia ciudad bbva", detalle="Pago con tarjeta", importe="-5.00", saldo="25.00"),
        ],
    )
    apply_local(conn)
    figures = kpis(conn, "todos")
    assert Decimal(figures["ingresos"]) == Decimal("100.00")
    assert Decimal(figures["consumo"]) == Decimal("5.00")
    assert Decimal(figures["ahorro"]) == Decimal("50.00")
    assert Decimal(figures["bizum_personas"]) == Decimal("20.00")
    assert Decimal(figures["resultado"]) == Decimal("25.00")
    series = evolucion(conn)
    assert series[0]["mes"] == "2026-01"
    assert Decimal(series[0]["consumo"]) == Decimal("5.00")
    assert series[0]["completo"] is False
    conn.close()


def test_edge_months_are_incomplete_and_the_middle_is_not():
    conn = _db("meses.sqlite")
    upsert_movements(
        conn,
        [
            _row(fecha="2025-12-12", importe="10.00", saldo="10.00", direccion="ingreso", concepto="Abono de nómina"),
            _row(fecha="2026-06-15", importe="-2.00", saldo="8.00", concepto="Delikia ciudad bbva", detalle="Pago con tarjeta"),
            _row(fecha="2026-11-13", importe="-1.00", saldo="7.00", concepto="Uber *trip", detalle="Pago con tarjeta"),
        ],
    )
    apply_local(conn)
    found = {item["mes"]: item["completo"] for item in meses(conn)["meses"]}
    assert found["2025-12"] is False
    assert found["2026-06"] is True
    assert found["2026-11"] is False
    conn.close()


def test_same_movement_is_not_inserted_twice_and_a_correction_wins():
    conn = _db("upsert.sqlite")
    row = _row(concepto="Delikia ciudad bbva", detalle="Pago con tarjeta", importe="-5.00", saldo="5.00")
    upsert_movements(conn, [row])
    upsert_movements(conn, [row])
    assert conn.execute("SELECT COUNT(*) AS n FROM movimientos").fetchone()["n"] == 1
    apply_local(conn, [("delikia", "compras")])
    stored = conn.execute("SELECT categoria, origen_clasificacion FROM movimientos").fetchone()
    assert stored["categoria"] == "compras"
    assert stored["origen_clasificacion"] == "correccion"
    conn.close()


def test_jev_survives_a_later_rule_pass_until_a_correction():
    conn = _db("jev.sqlite")
    row = _row(concepto="Vip district virtual", detalle="Pago con tarjeta", importe="-12.00", saldo="12.00")
    upsert_movements(conn, [row])
    save_labels(conn, [{**row, "categoria": "ocio", "confianza": 0.8, "origen": "jev", "revisar": False}])
    apply_local(conn)
    stored = conn.execute("SELECT categoria, origen_clasificacion FROM movimientos").fetchone()
    assert stored["categoria"] == "ocio"
    assert stored["origen_clasificacion"] == "jev"
    apply_local(conn, [("vip district", "cafes_y_restaurantes")])
    stored = conn.execute("SELECT origen_clasificacion FROM movimientos").fetchone()
    assert stored["origen_clasificacion"] == "correccion"
    conn.close()


def test_balance_break_stays_out_of_the_database():
    TMP.mkdir(exist_ok=True)
    path = TMP / "roto.csv"
    path.write_text(
        "Fecha;Concepto;Importe;Saldo\n"
        "13/11/2026;Cafe;-1,00;10,00\n"
        "12/11/2026;Cafe;-1,00;10,00\n",
        encoding="utf-8",
    )
    conn = _db("roto.sqlite")
    result = import_file(conn, path)
    assert result["estado"] == "saltos"
    assert conn.execute("SELECT COUNT(*) AS n FROM movimientos").fetchone()["n"] == 0
    conn.close()


def test_bizum_between_people_is_not_spending():
    summary = summarize(
        [
            {"fecha": "2026-06-01", "importe": "-990.00", "categoria": "bizum_personas", "revisar": False, "concepto": "Bizum", "detalle": ""},
            {"fecha": "2026-06-02", "importe": "-5.00", "categoria": "cafes_y_restaurantes", "revisar": False, "concepto": "Cafe", "detalle": ""},
        ]
    )
    month = summary["meses"][0]
    assert Decimal(month["gastos"]) == Decimal("-5.00")
    assert Decimal(month["bizum_personas"]) == Decimal("-990.00")


def test_dashboard_html_asks_the_api_and_has_no_statement_amounts():
    html = WEB.read_text(encoding="utf-8")
    assert "fetch(" in html
    assert "/api/lectura" in html
    assert 'data-sort="fecha"' in html
    assert 'data-sort="importe"' in html
    assert "innerHTML" not in html
    assert re.search(r"\d[\d.]*,\d{2}", html) is None


def test_http_kpis_follow_the_database():
    path = TMP / "api.sqlite"
    TMP.mkdir(exist_ok=True)
    if path.exists():
        path.unlink()
    previous = os.environ.get("BBVA_GASTOS_DB")
    os.environ["BBVA_GASTOS_DB"] = str(path)
    httpd = None
    try:
        conn = connect()
        upsert_movements(
            conn,
            [
                _row(concepto="Abono de nómina", detalle="Matr", importe="100.00", saldo="100.00", direccion="ingreso"),
                _row(fecha="2026-01-13", concepto="Delikia ciudad bbva", detalle="Pago con tarjeta", importe="-5.00", saldo="95.00"),
            ],
        )
        conn.close()
        httpd = make_server("127.0.0.1", 0)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/kpis?mes=todos", timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert Decimal(body["consumo"]) == Decimal("5.00")
        assert Decimal(body["ingresos"]) == Decimal("100.00")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            html = response.read().decode("utf-8")
        assert "fetch(" in html
        assert "100.00" not in html
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if previous is None:
            os.environ.pop("BBVA_GASTOS_DB", None)
        else:
            os.environ["BBVA_GASTOS_DB"] = previous

import pytest

from bbva_gastos.db import apply_local, upsert_movements
from bbva_gastos.lectura import LecturaError, cifras_ajenas, clave_de, hechos, parrafos_desde_texto

from test_db import _db, _row


def test_parrafos_from_json_and_a_fence():
    assert parrafos_desde_texto('{"parrafos": ["Uno", " ", "Dos"]}') == ["Uno", "Dos"]
    assert parrafos_desde_texto('```json\n{"parrafos": ["Hola"]}\n```') == ["Hola"]


def test_parrafos_fall_back_to_lines_and_reject_empty_text():
    assert parrafos_desde_texto("Uno\n\nDos") == ["Uno", "Dos"]
    with pytest.raises(LecturaError):
        parrafos_desde_texto("   ")
    with pytest.raises(LecturaError):
        parrafos_desde_texto('{"parrafos": []}')


def test_hechos_reads_the_open_database():
    conn = _db("hechos.sqlite")
    upsert_movements(
        conn,
        [
            _row(concepto="Abono de nómina", detalle="Matr", importe="100.00", saldo="100.00", direccion="ingreso"),
            _row(fecha="2026-01-13", concepto="Delikia ciudad bbva", detalle="Pago con tarjeta", importe="-5.00", saldo="95.00"),
        ],
    )
    apply_local(conn)
    datos = hechos(conn, "todos")
    assert datos["periodo"] == "todos"
    assert datos["kpis"]["ingresos"] == "100.00"
    assert datos["kpis"]["consumo"] == "5.00"
    assert datos["movimientos_mayores"][0]["concepto"] == "Abono de nómina"
    assert clave_de(datos) == clave_de(hechos(conn, "todos"))
    assert datos["serie"][0]["bizum_personas"] == "0.00"


def test_a_reading_cannot_invent_a_number():
    datos = {"kpis": {"ingresos": "100.00"}, "serie": [{"mes": "2026-01", "bizum_personas": "15.00"}]}
    assert cifras_ajenas(["Son 100.00 en 2026 y el Bizum es 15.00."], datos) == []
    assert cifras_ajenas(["En junio fueron 2700.00."], datos) == ["2700.00"]
    assert cifras_ajenas(["Cafés: 1.998,55."], {"consumo": "-1998.55"}) == []

import argparse
import json
import threading
import time
import uuid
import webbrowser
from datetime import date, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from bbva_gastos.client import (
    DATA,
    ApiError,
    application,
    aspsps,
    auth_header,
    balances,
    callback_address,
    choose_redirect,
    create_session,
    load_key,
    load_session,
    save_session,
    start_authorization,
    transactions,
)
from bbva_gastos.db import (
    apply_local,
    connect,
    ensure_ready,
    estado,
    import_file,
    save_labels,
    scan_folder,
    upsert_movements,
)
from bbva_gastos.jev import JevError, classify, load_api_key
from bbva_gastos.normalize import normalize_transaction, pick_aspsp, to_canonical
from bbva_gastos.report import format_summary, summarize
from bbva_gastos.server import serve
from bbva_gastos.statement import balance_breaks, load_statement

MOVIMIENTOS = DATA / "movimientos.json"
CLASIFICADOS = DATA / "clasificados.json"
RESUMEN = DATA / "resumen.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Movimientos de BBVA: extracto manual o Enable Banking.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="Comprueba la clave y la app, sin entrar al banco.")

    connect = sub.add_parser("connect", help="Abre BBVA, guarda el consentimiento y la sesión.")
    connect.add_argument("--country", default="ES")
    connect.add_argument("--aspsp", default="BBVA")
    connect.add_argument("--psu-type", default="personal", choices=("personal", "business"))

    sync = sub.add_parser("sync", help="Descarga saldos y movimientos con la sesión guardada.")
    sync.add_argument("--days", type=int, default=180)

    cargar = sub.add_parser("cargar", help="Lee un PDF, Excel o CSV y lo guarda en la base.")
    cargar.add_argument("fichero")

    entrada = sub.add_parser("entrada", help="Carga los PDF, Excel y CSV nuevos de la carpeta entrada/.")
    entrada.add_argument("--vigilar", action="store_true", help="Repite la lectura cada cierto tiempo.")
    entrada.add_argument("--segundos", type=int, default=30)

    clasificar = sub.add_parser("clasificar", help="Rellena con Jev lo que las reglas dejan pendiente.")
    clasificar.add_argument("--key-file")
    clasificar.add_argument("--rehacer", action="store_true", help="Vuelve a preguntar a Jev lo que ya había clasificado.")

    sub.add_parser("resumen", help="Muestra ingresos, consumo y ahorro por mes.")

    servir = sub.add_parser("servir", help="Abre el panel. Lee la base en cada visita.")
    servir.add_argument("--host", default="127.0.0.1")
    servir.add_argument("--port", type=int, default=8766)

    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return check()
        if args.command == "connect":
            return connect_bank(args.country, args.aspsp, args.psu_type)
        if args.command == "cargar":
            return cargar_fichero(args.fichero)
        if args.command == "entrada":
            return vigilar_entrada(args.vigilar, args.segundos)
        if args.command == "clasificar":
            return clasificar_movimientos(args.key_file, args.rehacer)
        if args.command == "resumen":
            return mostrar_resumen()
        if args.command == "servir":
            return serve(args.host, args.port)
        return sync_bank(args.days)
    except JevError as error:
        print(error)
        return 1
    except ApiError as error:
        print(error)
        return 1


def cargar_fichero(fichero):
    rows, ignored, lector = load_statement(fichero)
    if not rows:
        raise SystemExit(f"No hay movimientos en {fichero}")
    breaks = balance_breaks(rows)
    dates = sorted(row["fecha"] for row in rows)
    print(f"Lectura: {lector}")
    print(f"Movimientos: {len(rows)}")
    print(f"Del {dates[0]} al {dates[-1]}")
    print(f"Saltos de saldo: {breaks}")
    if ignored:
        print(f"Líneas no leídas: {len(ignored)}")
        for line in ignored[:8]:
            print(f"  {line}")
    if breaks:
        print("Hay saltos de saldo. No guardo este fichero en la base: la lectura no cuadra.")
        return 1
    DATA.mkdir(parents=True, exist_ok=True)
    MOVIMIENTOS.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    conn = connect()
    try:
        result = import_file(conn, fichero)
        apply_local(conn)
        _print_carga(result)
        _print_estado(conn)
    finally:
        conn.close()
    return 0


def vigilar_entrada(vigilar, segundos):
    pausa = max(segundos, 5)
    conn = connect()
    try:
        while True:
            resultados = scan_folder(conn)
            if not resultados:
                print("No hay PDF, Excel ni CSV en entrada/.")
            for result in resultados:
                if vigilar and result["estado"] == "ya cargado":
                    continue
                _print_carga(result)
            if resultados and any(item["estado"] == "cargado" for item in resultados):
                _print_estado(conn)
            if not vigilar:
                return 0
            time.sleep(pausa)
    except KeyboardInterrupt:
        print("\nParado.")
        return 0
    finally:
        conn.close()


def clasificar_movimientos(key_file, rehacer=False):
    conn = connect()
    try:
        total = ensure_ready(conn)
        if not total:
            raise SystemExit("No hay movimientos. Ejecuta primero: python -m bbva_gastos cargar <fichero>")
        if rehacer:
            conn.execute(
                """UPDATE movimientos
                   SET categoria=NULL, confianza=NULL, origen_clasificacion='pendiente', revisar=1
                   WHERE origen_clasificacion='jev'"""
            )
            conn.commit()
            apply_local(conn)
        rows = [dict(row) for row in conn.execute("SELECT * FROM movimientos ORDER BY fecha")]
        print(format_summary(summarize(rows)))
        pending = [row for row in rows if row["origen_clasificacion"] == "pendiente"]
        if not pending:
            print("No queda nada pendiente. Jev no hace falta.")
            _write_classified(rows)
            return 0
        try:
            api_key = load_api_key(key_file)
        except SystemExit as error:
            print(error)
            print(f"Pendientes de Jev: {len(pending)}. El panel ya muestra el resto.")
            _write_classified(rows)
            return 1
        classified, usage = classify(pending, api_key)
        save_labels(conn, classified)
        rows = [dict(row) for row in conn.execute("SELECT * FROM movimientos ORDER BY fecha")]
        print(format_summary(summarize(rows)))
        print(
            f"Jev: {usage['llamadas']} llamadas, {usage['tokens_entrada']} tokens, "
            f"{usage['conceptos']} conceptos distintos"
        )
        _write_classified(rows)
        _print_estado(conn)
        return 0
    finally:
        conn.close()


def mostrar_resumen():
    conn = connect()
    try:
        total = ensure_ready(conn)
        if not total:
            raise SystemExit("No hay movimientos. Ejecuta primero: python -m bbva_gastos cargar <fichero>")
        rows = [dict(row) for row in conn.execute("SELECT * FROM movimientos ORDER BY fecha")]
    finally:
        conn.close()
    summary = summarize(rows)
    DATA.mkdir(parents=True, exist_ok=True)
    RESUMEN.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(format_summary(summary))
    return 0


def _write_classified(rows):
    DATA.mkdir(parents=True, exist_ok=True)
    CLASIFICADOS.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _print_carga(result):
    estado_carga = result["estado"]
    if estado_carga == "ya cargado":
        print(f"{result['nombre']}: ya estaba cargado. No lo duplico.")
        return
    if estado_carga == "saltos":
        print(
            f"{result['nombre']}: lectura {result['lector']}, {result['movimientos']} movimientos, "
            f"{result['saltos']} saltos de saldo. No entra en la base."
        )
        return
    if estado_carga == "vacio":
        print(f"{result['nombre']}: no tiene movimientos.")
        return
    print(
        f"{result['nombre']}: lectura {result['lector']}, {result['movimientos']} movimientos, "
        f"del {result['desde']} al {result['hasta']} "
        f"({result['nuevos']} nuevos, {result['actualizados']} actualizados)."
    )


def _print_estado(conn):
    snapshot = estado(conn)
    print(
        f"Base {snapshot['base']}: {snapshot['movimientos']} movimientos, "
        f"{snapshot['pendientes']} sin clasificar."
    )


def check():
    headers = _headers()
    app = application(headers)
    print(f"App: {app.get('name')} ({app.get('environment')})")
    print(f"Redirects: {', '.join(app.get('redirect_urls') or [])}")
    print("La clave firma y Enable Banking acepta la app.")
    return 0


def connect_bank(country, aspsp_query, psu_type):
    headers = _headers()
    app = application(headers)
    redirect_url = choose_redirect(app)
    host, port, path = callback_address(redirect_url)
    bank = pick_aspsp(aspsps(headers, country), aspsp_query)
    state = str(uuid.uuid4())
    started = start_authorization(headers, bank, redirect_url, psu_type, state)
    print(f"Banco: {bank['name']} ({bank['country']})")
    print("Se abre el navegador. Entra en BBVA y aprueba el aviso en la app.")
    result = _wait_for_callback(host, port, path, started["url"])
    if result.get("state") != state:
        raise SystemExit("El state de la vuelta no coincide. No se ha creado la sesión.")
    if result.get("error"):
        raise SystemExit(
            f"BBVA o Enable Banking canceló el acceso: {result.get('error_description') or result['error']}"
        )
    if not result.get("code"):
        raise SystemExit(f"La vuelta no trae code. Parámetros: {result}")
    session = create_session(headers, result["code"])
    save_session(session)
    accounts = session.get("accounts") or []
    print(f"Sesión guardada. Cuentas visibles: {len(accounts)}")
    for account in accounts:
        iban = ((account.get("account_id") or {}).get("iban") or "")
        tail = iban[-4:] if len(iban) >= 4 else "----"
        label = account.get("name") or account.get("product") or account.get("cash_account_type") or "cuenta"
        print(f"  {label} ···{tail}")
    if not accounts:
        print(
            "La sesión no trae cuentas. En una app de producción restringida hay que "
            "enlazar antes cada cuenta con 'Activate by linking accounts' en el panel de Enable Banking."
        )
    return 0


def sync_bank(days):
    headers = _headers()
    session = load_session()
    accounts = session.get("accounts") or []
    if not accounts:
        raise SystemExit("La sesión no tiene cuentas. Vuelve a ejecutar connect después de enlazarlas.")
    date_from = (date.today() - timedelta(days=days)).isoformat()
    movements = []
    balance_rows = []
    for account in accounts:
        uid = account["uid"]
        balance_rows.append({"account_uid": uid, "balances": balances(headers, uid)})
        raw = transactions(headers, uid, date_from)
        movements.extend(normalize_transaction(item, account) for item in raw)
        print(f"{uid[:8]}… {len(raw)} movimientos")
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "balances.json").write_text(
        json.dumps(balance_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (DATA / "movements.json").write_text(
        json.dumps(movements, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    canonical = []
    omitted = 0
    for item in movements:
        row = to_canonical(item)
        if not row["fecha"]:
            omitted += 1
            continue
        canonical.append(row)
    conn = connect()
    try:
        inserted, updated = upsert_movements(conn, canonical)
        ensure_ready(conn)
        print(f"En la base: {inserted} nuevos, {updated} actualizados.")
        if omitted:
            print(f"Omitidos sin fecha: {omitted}.")
        _print_estado(conn)
    finally:
        conn.close()
    _print_summary(movements)
    print(f"Copia del banco en {DATA / 'movements.json'}")
    return 0


def _headers():
    application_id, private_key = load_key()
    return auth_header(application_id, private_key)


def _wait_for_callback(host, port, path, url):
    box = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != path:
                self.send_response(404)
                self.end_headers()
                return
            box["query"] = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            body = "Listo. Vuelve a la terminal.".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    server = HTTPServer((host, port), Handler)
    server.timeout = 300
    thread = threading.Thread(target=lambda: server.handle_request(), daemon=True)
    thread.start()
    webbrowser.open(url)
    thread.join(timeout=300)
    server.server_close()
    if "query" not in box:
        raise SystemExit("No llegó la vuelta del banco en 5 minutos.")
    return box["query"]


def _print_summary(movements):
    credits = Decimal("0")
    debits = Decimal("0")
    for movement in movements:
        amount = Decimal(movement["amount"])
        if amount >= 0:
            credits += amount
        else:
            debits += amount
    print(f"Movimientos: {len(movements)}")
    print(f"Ingresos: {credits:.2f}  Gastos: {debits:.2f}")


if __name__ == "__main__":
    raise SystemExit(main())

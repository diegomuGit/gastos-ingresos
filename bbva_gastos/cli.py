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
    connect,
    ensure_ready,
    estado,
    import_file,
    report_rows,
    save_labels,
    scan_folder,
    upsert_movements,
)
from bbva_gastos.jev import JevError, classify, load_api_key
from bbva_gastos.normalize import normalize_transaction, pick_aspsp, to_canonical
from bbva_gastos.report import format_summary, summarize
from bbva_gastos.server import serve
from bbva_gastos.wealth import save_bank_close

CLASIFICADOS = DATA / "clasificados.json"
RESUMEN = DATA / "resumen.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Movimientos de BBVA: extracto manual o Enable Banking.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="Comprueba la clave y la app, sin entrar al banco.")

    connect_parser = sub.add_parser("connect", help="Abre BBVA, guarda el consentimiento y la sesión.")
    connect_parser.add_argument("--country", default="ES")
    connect_parser.add_argument("--aspsp", default="BBVA")
    connect_parser.add_argument("--psu-type", default="personal", choices=("personal", "business"))

    sync = sub.add_parser("sync", help="Descarga saldos y movimientos con la sesión guardada.")
    sync.add_argument("--days", type=int, default=180)

    cargar = sub.add_parser("cargar", help="Lee un PDF, Excel o CSV y lo guarda en la base.")
    cargar.add_argument("fichero")
    cargar.add_argument("--desde", help="Primer día de cobertura que figura en el extracto (AAAA-MM-DD).")
    cargar.add_argument("--hasta", help="Último día de cobertura que figura en el extracto (AAAA-MM-DD).")
    cargar.add_argument("--cuenta", default="", help="Identificador estable de la cuenta (opcional).")

    saldo = sub.add_parser('saldo-bbva', help='Registra un saldo de cierre de BBVA en EUR para una fecha.')
    saldo.add_argument('fecha', help='Fecha del cierre que indica el banco (AAAA-MM-DD).')
    saldo.add_argument('saldo', help='Saldo del extracto en euros, con punto decimal.')
    saldo.add_argument('--cuenta', default='', help='El mismo identificador que usaste al importar esa cuenta.')

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
            return cargar_fichero(args.fichero, desde=args.desde, hasta=args.hasta, cuenta=args.cuenta)
        if args.command == 'saldo-bbva':
            conn = connect()
            try:
                save_bank_close(conn, args.fecha, args.saldo, args.cuenta)
            finally:
                conn.close()
            print(f'Saldo de cierre guardado para {args.fecha}.')
            return 0
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
    except (ApiError, OSError, ValueError) as error:
        print(error)
        return 1


def cargar_fichero(fichero, **coverage):
    conn = connect()
    try:
        result = import_file(conn, fichero, **coverage)
        _print_carga(result)
        _print_estado(conn)
    finally:
        conn.close()
    return int(result['estado'] in {'vacio', 'saltos'})


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
                return int(any(item['estado'] in {'error', 'saltos'} for item in resultados))
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
        rows = report_rows(conn)
        print(format_summary(summarize(rows)))
        pending = [row for row in rows if row["origen_clasificacion"] == "pendiente"
                   or (rehacer and row["origen_clasificacion"] == "jev")]
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
        classified, usage = classify(pending, api_key, refresh=rehacer)
        save_labels(conn, classified)
        rows = report_rows(conn)
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
        rows = report_rows(conn)
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
    if estado_carga == 'error':
        print(f"{result['nombre']}: {result['error']}")
        return
    if estado_carga == "cobertura actualizada":
        print(f"{result['nombre']}: cobertura actualizada sin importar movimientos de nuevo.")
        return
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
        if row['divisa'].upper() != 'EUR':
            raise ValueError('El panel solo admite cuentas en EUR.')
        row['divisa'] = 'EUR'
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

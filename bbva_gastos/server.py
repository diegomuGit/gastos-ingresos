"""Panel local. Sirve el HTML y lee las cifras de SQLite en cada petición."""

import csv
import io
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bbva_gastos.categories import ETIQUETAS, GASTO, INGRESO
from bbva_gastos.db import (
    apply_local,
    categorias,
    connect,
    duplicate_candidates,
    ensure_ready,
    estado,
    evolucion,
    insights,
    kpis,
    query_movimientos,
    reconcile,
    set_manual_category,
    meses,
)
from bbva_gastos.lectura import LecturaError, generar, obtener
from bbva_gastos.portfolio import PORTFOLIO, chart_path, local_page, snapshot
from bbva_gastos.transfers import annotate_portfolio, decide as decide_transfer, review as review_transfers
from bbva_gastos.wealth import evolution as wealth_evolution, summary as wealth_summary

WEB = Path(__file__).resolve().parent / "web"
TOKENS = Path(__file__).resolve().parent.parent / "assets" / "bbva-tokens.css"
HOST = "127.0.0.1"
PORT = 8766


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/api/categoria-manual", "/api/conciliar", "/api/conciliar-traspaso"}:
            return self._edit(parsed.path)
        if parsed.path != "/api/lectura":
            self.send_error(404)
            return
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        mes = query.get("mes") or "todos"
        if mes != "todos" and not _month(mes):
            return self._json(400, {"error": "El mes tiene que ser AAAA-MM o todos."})
        try:
            conn = connect()
            try:
                apply_local(conn)
                payload = generar(conn, mes, forzar=query.get("nuevo") == "1")
            finally:
                conn.close()
        except (ValueError, LecturaError) as error:
            return self._json(400, {"error": str(error)})
        return self._json(200, payload)

    def _edit(self, path):
        if not self._local_request():
            return self._json(403, {"error": "Edita desde el dashboard local."})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if self.headers.get_content_type() != 'application/json' or not 0 < length <= 4096:
                raise ValueError('Se esperaba una solicitud JSON.')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Solicitud no válida.')

            def identifier(key):
                value = data.get(key)
                if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
                    raise ValueError('Identificador de movimiento no válido.')
                return value

            conn = connect()
            try:
                if path == '/api/categoria-manual':
                    if 'categoria' not in data:
                        raise ValueError('Indica la categoría que quieres guardar.')
                    set_manual_category(conn, identifier('hash'), data['categoria'])
                elif path == '/api/conciliar-traspaso':
                    decide_transfer(conn, identifier('banco'), identifier('aporte'), data.get('accion'))
                else:
                    action = data.get('accion')
                    if action not in ('duplicado', 'distintos', 'deshacer'):
                        raise ValueError('Decisión no válida.')
                    reconcile(conn, identifier('primero'), identifier('segundo'), action,
                              identifier('excluido') if action == 'duplicado' else None)
            finally:
                conn.close()
        except LookupError as error:
            return self._json(404, {'error': str(error)})
        except ValueError as error:
            return self._json(400, {'error': str(error)})
        return self._json(200, {'ok': True})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._body(local_page(WEB / "index.html", "gastos"), "text/html; charset=utf-8")
        if parsed.path == "/inversion":
            return self._body(local_page(PORTFOLIO / "Index.html", "inversion"), "text/html; charset=utf-8")
        if parsed.path == "/inversion/chart.js":
            return self._file(chart_path(), "text/javascript; charset=utf-8")
        if parsed.path == "/api/inversion":
            if not self._local_request():
                return self._json(403, {"error": "La cartera local solo está disponible desde este equipo."})
            try:
                data = snapshot()
                conn = connect()
                try:
                    conn.execute('BEGIN')
                    payload = annotate_portfolio(conn, data)
                    payload.pop('conciliacionBBVA', None)
                    payload.pop('flujosPatrimonio', None)
                finally:
                    conn.close()
                return self._json(200, payload)
            except ValueError as error:
                return self._json(404, {"error": str(error)})
        if parsed.path == "/finanzas.css":
            return self._file(TOKENS.with_name("finanzas.css"), "text/css; charset=utf-8")
        if parsed.path == "/bbva-tokens.css":
            return self._file(TOKENS, "text/css; charset=utf-8")
        if parsed.path == "/app.css":
            return self._file(WEB / "app.css", "text/css; charset=utf-8")
        if parsed.path == "/app.js":
            return self._file(WEB / "app.js", "text/javascript; charset=utf-8")
        if parsed.path == "/patrimonio.js":
            return self._file(WEB / "patrimonio.js", "text/javascript; charset=utf-8")
        if parsed.path == "/patrimonio-grafico.js":
            return self._file(WEB / "patrimonio-grafico.js", "text/javascript; charset=utf-8")
        if parsed.path.startswith("/api/"):
            return self._api(parsed)
        self.send_error(404)

    def log_message(self, fmt, *args):
        return

    def _local_request(self):
        allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        host = self.headers.get("Host")
        origin = self.headers.get("Origin")
        return self.client_address[0] == "127.0.0.1" and host in allowed and origin in {None, f"http://{host}"}

    def _file(self, path, content_type):
        if not path.exists():
            self.send_error(404)
            return
        return self._body(path.read_bytes(), content_type)

    def _body(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _api(self, parsed):
        wealth_paths = {'/api/patrimonio', '/api/patrimonio/evolucion'}
        if parsed.path in wealth_paths | {'/api/traspasos'} and not self._local_request():
            return self._json(403, {'error': 'Este recurso solo está disponible desde este equipo.'})
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        mes = query.get("mes") or "todos"
        if mes != "todos" and not _month(mes):
            return self._json(400, {"error": "El mes tiene que ser AAAA-MM o todos."})
        try:
            conn = connect()
            try:
                if parsed.path not in wealth_paths:
                    apply_local(conn)
                conn.execute("BEGIN")
                payload = _route(conn, parsed.path, mes, query)
            finally:
                conn.close()
        except ValueError as error:
            return self._json(400, {"error": str(error)})
        if payload is None:
            return self._json(404, {"error": "No existe ese recurso."})
        if parsed.path == "/api/movimientos.csv":
            body = _movimientos_csv(payload["movimientos"]).encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="movimientos-{mes}.csv"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        return self._json(200, payload)


def _month(value):
    return bool(re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value)) and int(value[:4]) > 0


def _movimientos_csv(rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Fecha", "Concepto", "Detalle", "Categoría", "Origen", "Importe", "Revisar"])

    def safe(value):
        # Un concepto importado no debe convertirse en una fórmula al abrirlo.
        value = str(value)
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value

    for row in rows:
        writer.writerow([
            row["fecha"], safe(row["concepto"]), safe(row["detalle"]), row["etiqueta"],
            row["origen"], row["importe"].replace(".", ","), "Sí" if row["revisar"] else "No",
        ])
    return output.getvalue()


def _movement_options(query):
    options = {key: query.get(key, "") for key in ("categoria", "direccion", "origen")}
    allowed = {
        "categoria": set(ETIQUETAS), "direccion": {"ingreso", "gasto"},
        "origen": {"traspaso", "manual", "regla", "correccion", "jev", "pendiente"},
        "orden": {"fecha", "concepto", "etiqueta", "origen", "importe"},
        "sentido": {"asc", "desc"}, "revisar": {"0", "1"},
    }
    options.update(orden=query.get("orden", "fecha"), sentido=query.get("sentido", "desc"),
                   revisar=query.get("revisar", "0"))
    for key, values in allowed.items():
        if options[key] not in values and not (key in {"categoria", "direccion", "origen"} and not options[key]):
            raise ValueError(f"Valor no válido para {key}.")
    try:
        limit, offset = int(query.get("limit", "50")), int(query.get("offset", "0"))
    except ValueError as error:
        raise ValueError("limit y offset deben ser números enteros.") from error
    if limit < 1 or offset < 0:
        raise ValueError("limit debe ser positivo y offset no puede ser negativo.")
    return {**options, "revisar": options["revisar"] == "1", "texto": query.get("texto", ""),
            "limit": min(limit, 200), "offset": offset}


def _route(conn, path, mes, query):
    if path == '/api/patrimonio':
        return wealth_summary(conn, query.get('fecha'))
    if path == '/api/patrimonio/evolucion':
        return wealth_evolution(conn, query.get('periodo', 'todo'))
    if path == '/api/traspasos':
        return review_transfers(conn, mes)
    if path == "/api/dashboard":
        return {
            "meses": meses(conn), "kpis": kpis(conn, mes), "evolucion": evolucion(conn),
            "categorias": categorias(conn, mes), "insights": insights(conn, mes),
            "lectura": obtener(conn, mes), "estado": estado(conn),
            "conciliacion": duplicate_candidates(conn, mes),
            "categorias_editables": {direction: {key: ETIQUETAS[key] for key in group}
                                    for direction, group in [('ingreso', INGRESO), ('gasto', GASTO)]},
        }
    if path == "/api/meses":
        return meses(conn)
    if path == "/api/evolucion":
        return {"meses": evolucion(conn)}
    if path == "/api/kpis":
        return kpis(conn, mes)
    if path == "/api/categorias":
        return categorias(conn, mes)
    if path == "/api/insights":
        return insights(conn, mes)
    if path == "/api/lectura":
        return obtener(conn, mes)
    if path == "/api/estado":
        return estado(conn)
    if path in {"/api/movimientos", "/api/movimientos.csv"}:
        options = _movement_options(query)
        if path.endswith(".csv"):
            options.update(limit=None, offset=0)
        return query_movimientos(conn, mes, **options)
    return None


def make_server(host=HOST, port=PORT):
    return ThreadingHTTPServer((host, port), Handler)


def serve(host=HOST, port=PORT):
    conn = connect()
    try:
        total = ensure_ready(conn)
        status = estado(conn)
    finally:
        conn.close()
    try:
        httpd = make_server(host, port)
    except OSError as error:
        raise SystemExit(f"No puedo abrir {host}:{port}. {error}") from error
    print(f"Mis finanzas en http://{host}:{port} (gastos e inversión)")
    print(f"Movimientos en la base: {total}. Sin clasificar: {status['pendientes']}.")
    print("Ctrl+C para parar.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nParado.")
    finally:
        httpd.server_close()
    return 0

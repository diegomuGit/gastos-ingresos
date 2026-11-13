"""Panel local. Sirve el HTML y lee las cifras de SQLite en cada petición."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bbva_gastos.categories import ETIQUETAS
from bbva_gastos.db import (
    apply_local,
    categorias,
    connect,
    ensure_ready,
    estado,
    evolucion,
    insights,
    kpis,
    list_movimientos,
    meses,
)
from bbva_gastos.lectura import LecturaError, generar, obtener

WEB = Path(__file__).resolve().parent / "web"
TOKENS = Path(__file__).resolve().parent.parent / "assets" / "bbva-tokens.css"
HOST = "127.0.0.1"
PORT = 8766


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        parsed = urlparse(self.path)
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

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._file(WEB / "index.html", "text/html; charset=utf-8")
        if parsed.path == "/bbva-tokens.css":
            return self._file(TOKENS, "text/css; charset=utf-8")
        if parsed.path == "/app.css":
            return self._file(WEB / "app.css", "text/css; charset=utf-8")
        if parsed.path.startswith("/api/"):
            return self._api(parsed)
        self.send_error(404)

    def log_message(self, fmt, *args):
        return

    def _file(self, path, content_type):
        if not path.exists():
            self.send_error(404)
            return
        body = path.read_bytes()
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
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        mes = query.get("mes") or "todos"
        if mes != "todos" and not _month(mes):
            return self._json(400, {"error": "El mes tiene que ser AAAA-MM o todos."})
        try:
            conn = connect()
            try:
                apply_local(conn)
                payload = _route(conn, parsed.path, mes, query)
            finally:
                conn.close()
        except ValueError as error:
            return self._json(400, {"error": str(error)})
        if payload is None:
            return self._json(404, {"error": "No existe ese recurso."})
        return self._json(200, payload)


def _month(value):
    parts = value.split("-")
    return len(parts) == 2 and len(parts[0]) == 4 and parts[0].isdigit() and parts[1].isdigit() and 1 <= int(parts[1]) <= 12


def _route(conn, path, mes, query):
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
    if path == "/api/movimientos":
        categoria = query.get("categoria") or ""
        if categoria and categoria not in ETIQUETAS:
            raise ValueError("Esa categoría no existe.")
        try:
            limit = int(query.get("limit") or 50)
        except ValueError as error:
            raise ValueError("limit tiene que ser un número.") from error
        limit = min(max(limit, 1), 2000)
        return {"movimientos": list_movimientos(conn, mes, categoria or None, limit)}
    return None


def make_server(host=HOST, port=PORT):
    return ThreadingHTTPServer((host, port), Handler)


def serve(host=HOST, port=PORT):
    conn = connect()
    try:
        total = ensure_ready(conn)
        snapshot = estado(conn)
    finally:
        conn.close()
    try:
        httpd = make_server(host, port)
    except OSError as error:
        raise SystemExit(f"No puedo abrir {host}:{port}. {error}") from error
    print(f"Dashboard en http://{host}:{port}")
    print(f"Movimientos en la base: {total}. Sin clasificar: {snapshot['pendientes']}.")
    print("Ctrl+C para parar.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nParado.")
    finally:
        httpd.server_close()
    return 0

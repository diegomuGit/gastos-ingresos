"""Lectura en español. Un Gemini barato narra los totales que ya están en la base."""

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal

import requests

from bbva_gastos.db import categorias, evolucion, kpis, list_movimientos, money
from bbva_gastos.jev import load_api_key

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-2.5-flash-lite"


class LecturaError(RuntimeError):
    pass


def ensure_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lecturas (
            clave TEXT PRIMARY KEY,
            mes TEXT NOT NULL,
            modelo TEXT NOT NULL,
            parrafos TEXT NOT NULL,
            creado TEXT NOT NULL
        )
        """
    )


def hechos(conn, mes="todos"):
    periodo = mes or "todos"
    serie = evolucion(conn)
    for item in serie:
        aparte = categorias(conn, item["mes"])["aparte"]
        bizum = next((row["importe"] for row in aparte if row["categoria"] == "bizum_personas"), "0.00")
        item["bizum_personas"] = money(abs(Decimal(bizum)))
    if periodo != "todos":
        serie = [item for item in serie if item["mes"] == periodo]
    grupos = categorias(conn, periodo)
    mayores = list_movimientos(conn, periodo, None, 12)
    return {
        "periodo": periodo,
        "kpis": kpis(conn, periodo),
        "serie": serie,
        "consumo": grupos["consumo"][:8],
        "ingresos": grupos["ingresos"],
        "aparte": grupos["aparte"],
        "movimientos_mayores": [
            {
                "fecha": row["fecha"],
                "concepto": row["concepto"],
                "detalle": row["detalle"],
                "importe": row["importe"],
                "categoria": row["etiqueta"],
            }
            for row in mayores
        ],
        "notas": [
            "El consumo no incluye ahorro e inversión ni Bizum a personas.",
            "Los movimientos sin categoría no entran en el consumo.",
            "Un mes incompleto no cubre desde el día 1 hasta el último día.",
        ],
    }


def clave_de(datos):
    raw = json.dumps({"v": 2, "datos": datos}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parrafos_desde_texto(content):
    text = (content or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        lines = [line.strip(" -\t") for line in (content or "").splitlines() if line.strip()]
        if not lines:
            raise LecturaError("El modelo no devolvió texto.")
        return lines[:6]
    paras = data.get("parrafos") if isinstance(data, dict) else None
    if not isinstance(paras, list) or not any(str(item).strip() for item in paras):
        raise LecturaError("El modelo no devolvió párrafos.")
    return [str(item).strip() for item in paras if str(item).strip()][:6]


_NUM = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?")


def _a_decimal(token):
    text = str(token).strip().replace("−", "-").replace("–", "-")
    sign = -1 if text.startswith("-") else 1
    text = text.lstrip("+-")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", text):
        text = text.replace(".", "")
    return (Decimal(text) * sign).quantize(Decimal("0.01"))


def _valores(datos):
    valores = set()

    def add(token):
        try:
            valor = _a_decimal(token)
        except Exception:
            return
        valores.add(valor)
        if valor < 0:
            valores.add(abs(valor))

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, bool) or node is None:
            return
        elif isinstance(node, (int, float, Decimal)):
            add(node)
        elif isinstance(node, str):
            if re.fullmatch(r"-?\d+(?:[.,]\d+)?", node):
                add(node)
            else:
                for token in _NUM.findall(node):
                    add(token)

    walk(datos)
    return valores


def cifras_ajenas(parrafos, datos):
    permitidas = _valores(datos)
    ajenas = []
    for parrafo in parrafos:
        for token in _NUM.findall(parrafo):
            try:
                valor = _a_decimal(token)
            except Exception:
                continue
            if valor not in permitidas:
                ajenas.append(token)
    return ajenas


def _pedir(api_key, datos):
    parrafos = _completar(api_key, datos, False)
    if cifras_ajenas(parrafos, datos):
        parrafos = _completar(api_key, datos, True)
        ajenas = cifras_ajenas(parrafos, datos)
        if ajenas:
            muestra = ", ".join(ajenas[:8])
            raise LecturaError(f"El modelo usó cifras que no están en la base ({muestra}). Vuelve a generar.")
    return parrafos


def _completar(api_key, datos, reintento):
    body = {
        "model": MODEL,
        "temperature": 0.2,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Eres un analista de las finanzas personales de una cuenta BBVA en España. "
                    "Escribes en español, con frases cortas y concretas. "
                    "Usas únicamente las cifras y los nombres que vienen en el JSON. "
                    "No inventas comercios, fechas ni importes. "
                    "Cada párrafo tiene dos o tres frases: compara meses, señala la categoría que más pesa en el consumo, "
                    "separa el ahorro y los Bizum a personas del consumo, y nombra todos los meses con completo en false. "
                    "El Bizum de cada mes está en bizum_personas de la serie. No lo calcules desde un movimiento suelto. "
                    "Copia las cifras tal cual aparecen en el JSON. Si una cifra no está, no la escribas. "
                    "No te limites a repetir el total del periodo. "
                    "Devuelve un JSON con la clave parrafos: una lista de 4 a 6 párrafos."
                ),
            },
            {
                "role": "user",
                "content": (
                    "El intento anterior usó cifras que no están en el JSON. "
                    "Vuelve a escribir y copia solo cifras que aparezcan en los datos.\n"
                    if reintento
                    else ""
                )
                + json.dumps(datos, ensure_ascii=False),
            },
        ],
    }
    response = requests.post(
        CHAT_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    if response.status_code == 400 and "response_format" in body:
        body.pop("response_format")
        response = requests.post(
            CHAT_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
    if response.status_code >= 400:
        raise LecturaError(f"OpenRouter respondió {response.status_code}: {response.text[:300]}")
    payload = response.json()
    content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    return parrafos_desde_texto(content)


def _cache(conn, clave):
    ensure_table(conn)
    row = conn.execute(
        "SELECT parrafos, modelo, creado FROM lecturas WHERE clave = ?",
        (clave,),
    ).fetchone()
    if not row:
        return None
    return {
        "parrafos": json.loads(row["parrafos"]),
        "modelo": row["modelo"],
        "creado": row["creado"],
        "cache": True,
    }


def obtener(conn, mes="todos"):
    clave = clave_de(hechos(conn, mes))
    cached = _cache(conn, clave)
    if cached:
        return cached
    return {"parrafos": [], "modelo": MODEL, "cache": False}


def generar(conn, mes="todos", forzar=False):
    datos = hechos(conn, mes)
    clave = clave_de(datos)
    if not forzar:
        cached = _cache(conn, clave)
        if cached:
            return cached
    try:
        api_key = load_api_key()
    except SystemExit as error:
        raise LecturaError(str(error)) from error
    parrafos = _pedir(api_key, datos)
    creado = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ensure_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO lecturas (clave, mes, modelo, parrafos, creado) VALUES (?,?,?,?,?)",
        (clave, mes or "todos", MODEL, json.dumps(parrafos, ensure_ascii=False), creado),
    )
    conn.commit()
    return {"parrafos": parrafos, "modelo": MODEL, "creado": creado, "cache": False}

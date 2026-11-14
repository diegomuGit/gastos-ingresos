"""Puente local de solo lectura con la salida del cálculo de MyInvestor."""

import json
import os
from pathlib import Path

from bbva_gastos.client import DATA

PORTFOLIO = Path(__file__).resolve().parent.parent / "dashboard_myinvestor"


def snapshot():
    path = Path(os.environ.get("BBVA_PATRIMONIO_JSON") or DATA / "patrimonio.json")
    try:
        with path.open(encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except FileNotFoundError as error:
        raise ValueError("Todavía no hay una exportación local de MyInvestor. Consulta el README, genera data/patrimonio.json y pulsa Actualizar.") from error
    except (OSError, ValueError) as error:
        raise ValueError("No se pudo leer la exportación local de MyInvestor. Vuelve a generarla.") from error
    required = {"meta", "total", "perf", "mi", "crypto", "status"}
    if (not isinstance(data, dict) or not required <= data.keys()
            or not isinstance(data['status'], list)
            or not all(isinstance(data[key], dict) for key in required - {'status'})):
        raise ValueError("El archivo no es una exportación compatible del dashboard MyInvestor.")
    data.pop("spreadsheetUrl", None)
    return data


def local_page(path, section):
    nav = [('gastos', '/', 'Gastos y ahorro'), ('patrimonio', '/#patrimonio', 'Patrimonio conjunto'),
           ('inversion', '/inversion', 'Inversión')]
    links = ''.join('<a href="{}"{}>{}</a>'.format(url, ' aria-current="page"' if key == section else '', label)
                    for key, url, label in nav)
    content = path.read_text(encoding="utf-8")
    bar = f'<nav class="suite-nav" aria-label="Finanzas personales"><strong>Mis finanzas</strong>{links}</nav>'
    content = content.replace('</head>', '<link rel="stylesheet" href="/finanzas.css">\n</head>')
    content = content.replace('<body>', '<body>\n' + bar, 1)
    if section == 'inversion':
        content = content.replace('<body>', '<body data-patrimonio-local="true">', 1)
        content = content.replace('<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>',
                                  '<script src="/inversion/chart.js"></script>' if chart_path().is_file() else '')
    return content.encode("utf-8")


def chart_path():
    return PORTFOLIO / "node_modules" / "chart.js" / "dist" / "chart.umd.js"

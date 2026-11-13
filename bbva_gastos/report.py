from collections import defaultdict
from decimal import Decimal

from bbva_gastos.categories import GASTO, INGRESO

SIN_CLASIFICAR = {"", "sin_clasificar", "sin_categoria"}


def summarize(rows):
    months = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    counts = defaultdict(lambda: defaultdict(int))
    review = []
    for row in rows:
        month = row["fecha"][:7]
        category = row.get("categoria") or "sin_clasificar"
        amount = Decimal(row["importe"])
        months[month][category] += amount
        counts[month][category] += 1
        if row.get("revisar"):
            review.append(row)
    month_rows = []
    for month in sorted(months):
        ingresos = Decimal("0")
        gastos = Decimal("0")
        ahorro = Decimal("0")
        bizum = Decimal("0")
        sin_clasificar = Decimal("0")
        categories = []
        for category, total in sorted(months[month].items(), key=lambda item: abs(item[1]), reverse=True):
            categories.append(
                {
                    "categoria": category,
                    "importe": format(total, "f"),
                    "movimientos": counts[month][category],
                }
            )
            if category in INGRESO:
                ingresos += total
            elif category == "ahorro_inversion":
                ahorro += total
            elif category == "bizum_personas":
                bizum += total
            elif category in SIN_CLASIFICAR:
                sin_clasificar += total
            elif category in GASTO or total < 0:
                gastos += total
        month_rows.append(
            {
                "mes": month,
                "ingresos": format(ingresos, "f"),
                "gastos": format(gastos, "f"),
                "ahorro_inversion": format(ahorro, "f"),
                "bizum_personas": format(bizum, "f"),
                "sin_clasificar": format(sin_clasificar, "f"),
                "categorias": categories,
            }
        )
    return {
        "meses": month_rows,
        "revisar": [
            {
                "fecha": row["fecha"],
                "concepto": row["concepto"],
                "detalle": row.get("detalle", ""),
                "importe": row["importe"],
                "categoria": row.get("categoria"),
                "confianza": row.get("confianza"),
            }
            for row in review
        ],
    }


def format_summary(summary):
    lines = []
    for month in summary["meses"]:
        lines.append(
            f"{month['mes']}  ingresos {month['ingresos']}  "
            f"gastos {month['gastos']}  ahorro {month['ahorro_inversion']}  "
            f"bizum personas {month.get('bizum_personas', '0')}  "
            f"sin clasificar {month.get('sin_clasificar', '0')}"
        )
        for category in month["categorias"]:
            lines.append(
                f"  {category['categoria']:<24} {Decimal(category['importe']):>12}  "
                f"({category['movimientos']})"
            )
    lines.append(f"Por revisar: {len(summary['revisar'])}")
    return "\n".join(lines)

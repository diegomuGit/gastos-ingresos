import re

GASTO = {
    "alimentacion": "Supermercado, mercado o comida para preparar en casa.",
    "cafes_y_restaurantes": "Cafetería, bar, restaurante o comida y bebida fuera de casa, también si el pago es un Bizum a un local.",
    "transporte": "Metro, autobús, taxi, Uber, tren de cercanías, gasolina, parking o peaje.",
    "vivienda": "Alquiler, hipoteca, comunidad, luz, agua, gas, internet o teléfono de casa.",
    "salud": "Farmacia, médico, dentista o seguro de salud.",
    "deporte": "Gimnasio, pádel, tenis, club deportivo o cuota deportiva.",
    "suscripciones": "Cuota recurrente de software, inteligencia artificial, streaming, música o nube.",
    "compras": "Ropa, electrónica, Amazon, grandes almacenes u otro objeto comprado en una tienda.",
    "ocio": "Cine, conciertos, entradas u ocio que no es deporte, viaje ni restaurante.",
    "viajes": "Vuelo, hotel, alojamiento o transporte de un viaje.",
    "ahorro_inversion": "Traspaso a un bróker, fondo o cuenta de inversión. No es consumo.",
    "bizum_personas": "Bizum a una persona, regalo o reparto. No es consumo.",
    "banco": "Comisión, interés o cargo del propio banco.",
    "otros": "No encaja con claridad en ninguna categoría anterior.",
}

INGRESO = {
    "nomina": "Abono de nómina o salario.",
    "bizum_recibido": "Dinero recibido por Bizum de una persona.",
    "transferencia_recibida": "Transferencia entrante que no es la nómina.",
    "devolucion": "Abono, devolución o bonificación de un cargo anterior.",
    "otros_ingresos": "Otro ingreso que no es nómina, Bizum ni transferencia.",
}

ETIQUETAS = {
    "alimentacion": "Alimentación",
    "cafes_y_restaurantes": "Cafés y restaurantes",
    "transporte": "Transporte",
    "vivienda": "Vivienda",
    "salud": "Salud",
    "deporte": "Deporte",
    "suscripciones": "Suscripciones",
    "compras": "Compras",
    "ocio": "Ocio",
    "viajes": "Viajes",
    "ahorro_inversion": "Ahorro e inversión",
    "bizum_personas": "Bizum a personas",
    "banco": "Banco",
    "otros": "Otros gastos",
    "nomina": "Nómina",
    "bizum_recibido": "Bizum recibido",
    "transferencia_recibida": "Transferencia recibida",
    "devolucion": "Devoluciones",
    "otros_ingresos": "Otros ingresos",
    "sin_clasificar": "Sin clasificar",
}

# La primera regla cuya categoría encaja con el signo gana.
# Lo específico va antes que Bizum genérico, que se resuelve en match_rule.
REGLAS = (
    (re.compile(r"abono de n[oó]mina", re.I), "nomina"),
    (re.compile(r"\baeat\b", re.I), "devolucion"),
    (re.compile(r"bonificaci[oó]n", re.I), "devolucion"),
    (re.compile(r"myinvestor", re.I), "ahorro_inversion"),
    (re.compile(r"\bcomisi[oó]n\b", re.I), "banco"),
    (re.compile(r"liquidaci[oó]n de intereses", re.I), "banco"),
    (re.compile(r"liquidaci[oó]n de intereses", re.I), "otros_ingresos"),
    (re.compile(r"pago con tarjeta", re.I), "devolucion"),
    (re.compile(r"transferencia recibida|abono por transferencia", re.I), "transferencia_recibida"),
    (re.compile(r"openai|chatgpt|claude|anthropic|youtube|openrouter|netflix|spotify|icloud|\bgithub\b|google one|google play|packt", re.I), "suscripciones"),
    (re.compile(r"booking\.com|\bhotel\b|getyourguide|airbnb|ryanair|vueling|iberia|\bwizz\b|omio|ouigo|traventia|traventuria", re.I), "viajes"),
    (re.compile(r"\buber\b|\bmetro\b|crtm|parking|\btaxi\b|cabify|\bbolt\b|free2move|renfe|\bemt\b|\btransit\b|\balsa\b|locarent", re.I), "transporte"),
    (re.compile(r"farmacia|\bfcia\b|farmac|fisioterapia|\bfisio\b", re.I), "salud"),
    (re.compile(r"tenis|p[aá]del|tennispro|factory fit|gimnasio|club deportivo|tie break|\bgolf\b|baloncesto|pilobuilders|handball", re.I), "deporte"),
    (re.compile(r"amazon|media\s*markt|corte ingl|zalando|zala\s*ndo|\bzara\b|massimo dutti|\bnike\b|zapater|google store|hm es|hipercor|uniqlo|preciados|cos es", re.I), "compras"),
    (re.compile(r"teatro|\bcine\b|palacio de hielo", re.I), "ocio"),
    (re.compile(r"\bconsum\b|mercadona|carrefour|\blidl\b|\bdia\b|alcampo", re.I), "alimentacion"),
    (re.compile(r"delikia|expresso|cafeter[ií]a|honest greens|aspretto|restaurante|qmecomo|fontana|bareto|\bbuca\b|konoba|casa carmen|\bpizza\b|\bpoke\b|vermut|guinness|guiness|\bcerve\b|\bcafe\b|\bcena\b|\bcomida\b|coffee|coffe|honesto|\btonica\b|ramen|trattoria|piano bar|\b130[0-2]\b", re.I), "cafes_y_restaurantes"),
)

_BIZUM = re.compile(r"\bbizum\b", re.I)
_COMPRA_BIZUM = re.compile(r"compra\s*:", re.I)


def criteria_for(direccion):
    return INGRESO if direccion == "ingreso" else GASTO


def etiquetar(categoria):
    if not categoria:
        return ETIQUETAS["sin_clasificar"]
    return ETIQUETAS.get(categoria, categoria)


def match_rule(movement):
    allowed = criteria_for(movement.get("direccion"))
    text = f"{movement.get('concepto', '')} {movement.get('detalle', '')}"
    for pattern, category in REGLAS:
        if category not in allowed:
            continue
        if pattern.search(text):
            return category
    if _BIZUM.search(text):
        if movement.get("direccion") == "ingreso":
            return "bizum_recibido"
        if _COMPRA_BIZUM.search(text):
            return None
        return "bizum_personas"
    return None

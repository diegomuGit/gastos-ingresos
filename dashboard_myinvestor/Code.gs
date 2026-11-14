/**
 * Patrimonio de Diego — Dashboard web v4 (Google Apps Script + HtmlService)
 *
 * La hoja de cálculo es SOLO la base de datos: aquí se lee y se calcula todo. NUNCA se escribe en ella.
 *
 *   PATRIMONIO TOTAL = MyInvestor (último Total de Historico: fondos + acciones + efectivo)
 *                    + cripto     (último Valor_EUR de la pestaña Crypto)
 *
 * El archivo tiene dos partes separadas por una frontera explícita:
 *   1. ADAPTADOR (Apps Script): doGet, getDashboardData, openSpreadsheet_ y plainValues_. Es lo ÚNICO que toca
 *      servicios de Google: lee las pestañas y las pasa como matrices planas (texto y números) al cálculo.
 *   2. CÁLCULO PURO: computeDashboard_(tabs, opts) y todo lo que llama. Sin SpreadsheetApp, HtmlService, Utilities,
 *      Session ni DOM: recibe { 'Nombre de pestaña': [[celdas]] } y devuelve un objeto JSON. Se ejecuta tal cual en
 *      Node (harness.js) y se puede portar a TypeScript sin reescribirlo.
 * Index.html solo pinta lo que devuelve el cálculo (no calcula rentabilidades, contribuciones ni bases 100).
 *
 * Las pestañas se leen por NOMBRE DE CABECERA (no por letra de columna), de forma tolerante:
 * da igual mayúsculas, acentos o sufijos de unidad (Total = Total_EUR, Cantidad = Cantidad_BTC, TER = TER_%),
 * y puede haber filas de texto antes de la cabecera. Si una pestaña falta o está vacía, el resto funciona.
 *
 * Pestañas: Historico · "Stocks y Fondos" y/o Stocks + Fondos · Fondos_Ref · Targets · Flujos · Crypto · Crypto_Movs
 */

/* ===================== Configuración (datos, sin lógica) ===================== */

var SPREADSHEET_ID = 'TU_SPREADSHEET_ID';

/** Clave lógica -> nombres de pestaña aceptados (sin distinguir mayúsculas, acentos, espacios ni guiones). */
var TAB_ALIASES = {
  historico: ['Historico'],
  posiciones: ['Stocks y Fondos', 'Stocks_y_Fondos', 'Posiciones'],
  stocks: ['Stocks', 'Acciones'],
  fondos: ['Fondos'],
  fondosRef: ['Fondos_Ref'],
  targets: ['Targets', 'Objetivos'],
  flujos: ['Flujos'],
  crypto: ['Crypto', 'Cripto'],
  cryptoMovs: ['Crypto_Movs', 'Cripto_Movs']
};

var DEFAULT_TARGETS = { cash: 0.05, rv: 0.80, rf: 0.15 };
var DEFAULT_BENCHMARK = 'MSCI World';

// Geografía de acciones sueltas (la de los fondos sale de Fondos_Ref)
var STOCK_GEO = { GOOG: 'USA', GOOGL: 'USA', MA: 'USA', ASML: 'EU' };
var MARKET_GEO = { NDQ: 'USA', NASDAQ: 'USA', NYS: 'USA', NYSE: 'USA', ARCA: 'USA',
  AEX: 'EU', AMS: 'EU', XETRA: 'EU', GER: 'EU', PAR: 'EU', MC: 'EU', BME: 'EU', MIL: 'EU', LSE: 'EU', SWX: 'EU',
  TSE: 'JP', JPX: 'JP' };
var GEO_LABELS = { USA: 'EE. UU.', EU: 'Europa', EM: 'Emergentes', JP: 'Japón', OTHER: 'Otros' };

// Divisa en la que cotiza cada acción (los fondos publican su valor liquidativo ya en euros)
var STOCK_CCY = { GOOG: 'USD', GOOGL: 'USD', MA: 'USD', ASML: 'EUR' };
var MARKET_CCY = { NDQ: 'USD', NASDAQ: 'USD', NYS: 'USD', NYSE: 'USD', ARCA: 'USD',
  AEX: 'EUR', AMS: 'EUR', XETRA: 'EUR', GER: 'EUR', PAR: 'EUR', MC: 'EUR', BME: 'EUR', MIL: 'EUR' };

/** Nombre corto de cada activo en la vista diaria (si no está aquí, se acorta el nombre de la hoja). */
var SHORT_NAMES = { IE00BYX5NX33: 'MSCI World', IE0031786142: 'Emergentes', ES0165243025: 'Value', IE00B4NF1620: 'Japón',
  IE00BL1GVT98: 'RF corto plazo', GOOG: 'Alphabet', GOOGL: 'Alphabet', MA: 'Mastercard', ASML: 'ASML' };

/** Ventanas de la vista diaria y de los drivers. */
var DAILY_WINDOWS = [
  { key: '1d', label: '1 día' },
  { key: '20d', label: '20 días', n: 20 },
  { key: '3m', label: '3 meses', months: 3 },
  { key: 'ytd', label: 'YTD' }
];
/** Días que se pueden elegir uno a uno en la vista «1 día» (los de la ventana de 20 días). */
var DAY_PICKS = 20;

/**
 * Columnas de cada pestaña: campo -> alias aceptados, por orden de preferencia.
 * Un alias casa con la cabecera normalizada o con la cabecera sin sufijo de unidad (_EUR, _BTC, _%).
 * Un alias terminado en * casa por prefijo.
 */
var SPEC = {
  historico: {
    fecha: ['fecha', 'date', 'dia'],
    source: ['source', 'fuente', 'origen'],
    inversiones: ['inversiones', 'invertido'],
    fondos: ['fondos'],
    acciones: ['acciones', 'stocks'],
    efectivo: ['efectivo', 'cash', 'liquidez'],
    total: ['total', 'patrimonio', 'total_cartera'],
    aport: ['aport', 'aportaciones_netas_dia', 'aportaciones_netas', 'aportacion_neta', 'aportaciones', 'aport*'],
    nota: ['nota', 'notas', 'comentario']
  },
  posiciones: {
    fecha: ['fecha', 'date', 'dia'],
    source: ['source', 'fuente', 'origen'],
    tipo: ['tipo', 'type'],
    id: ['isin_ticker', 'isin', 'ticker', 'simbolo'],
    nombre: ['nombre', 'name', 'descripcion'],
    cuenta: ['cuenta', 'account'],
    valor: ['valor', 'valor_mercado'],
    cantidad: ['cantidad', 'participaciones', 'titulos', 'unidades'],
    nav: ['nav', 'valor_liquidativo', 'precio'],
    navDate: ['nav_date', 'fecha_nav', 'fecha_precio'],
    mercado: ['mercado', 'market', 'exchange'],
    coste: ['coste', 'cost'],
    plusvalia: ['plusvalia_latente', 'plusvalia'],
    rentPlataforma: ['rentabilidad_plataforma'],
    nota: ['nota', 'notas', 'comentario', 'nota*']
  },
  fondosRef: {
    isin: ['isin', 'isin_ticker'],
    nombre: ['nombre', 'name'],
    ter: ['ter'],
    clase: ['clase'],
    estilo: ['estilo'],
    divisa: ['divisa'],
    gestora: ['gestora'],
    rv: ['rv'],
    rf: ['rf'],
    benchmark: ['benchmark', 'indice'],
    actualizado: ['actualizado']
  },
  targets: {
    categoria: ['categoria', 'category'],
    target: ['target', 'objetivo']
  },
  flujos: {
    fecha: ['fecha', 'date', 'dia'],
    tipo: ['tipo', 'type'],
    importe: ['importe', 'amount'],
    nota: ['nota', 'notas', 'comentario']
  },
  crypto: {
    fecha: ['fecha', 'date', 'dia'],
    source: ['source', 'fuente', 'origen'],
    activo: ['activo', 'asset', 'moneda'],
    plataforma: ['plataforma', 'exchange', 'platform'],
    cantidad: ['cantidad', 'amount'],
    precio: ['precio', 'price'],
    valor: ['valor', 'value'],
    coste: ['coste', 'cost'],
    plusvalia: ['plusvalia_latente', 'plusvalia'],
    priceTime: ['price_time', 'hora_precio'],
    nota: ['nota', 'notas', 'comentario']
  },
  cryptoMovs: {
    fecha: ['fecha', 'date', 'dia'],
    par: ['par', 'pair', 'mercado'],
    tipo: ['tipo', 'type', 'side', 'lado'],
    cantidad: ['cantidad', 'amount', 'ejecutado'],
    acum: ['cantidad_acum', 'cantidad_acumulada', 'acum', 'saldo'],
    precio: ['precio', 'price'],
    total: ['total', 'importe'],
    comision: ['comision', 'fee']
  }
};

/* =====================================================================================================
 * 1) ADAPTADOR APPS SCRIPT — lo ÚNICO que usa servicios de Google (SpreadsheetApp, HtmlService, Utilities,
 *    Session). Lee la hoja, convierte las celdas en valores planos y llama al cálculo puro. Aquí no se calcula nada.
 * ===================================================================================================== */

function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('Patrimonio · MyInvestor + cripto')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

/** Llamada desde el HTML con google.script.run. Devuelve JSON (texto). Solo lee la hoja. */
function getDashboardData() {
  var ss = openSpreadsheet_();
  var tz = ss.getSpreadsheetTimeZone() || Session.getScriptTimeZone() || 'Europe/Madrid';
  var wanted = {};
  Object.keys(TAB_ALIASES).forEach(function (k) { TAB_ALIASES[k].forEach(function (n) { wanted[tabKey_(n)] = true; }); });
  var tabs = {};
  ss.getSheets().forEach(function (sh) {
    var name = sh.getName();
    if (!wanted[tabKey_(name)]) return;
    tabs[name] = sh.getLastRow() > 0 ? plainValues_(sh.getDataRange().getValues(), tz) : [];
  });
  var now = new Date();
  var out = computeDashboard_(tabs, { tz: tz, now: now.toISOString(), today: Utilities.formatDate(now, tz, 'yyyy-MM-dd') });
  out.spreadsheetUrl = ss.getUrl();
  return JSON.stringify(out);
}

function openSpreadsheet_() {
  var ss = null;
  try { ss = SpreadsheetApp.getActiveSpreadsheet(); } catch (e) { ss = null; }
  return ss || SpreadsheetApp.openById(SPREADSHEET_ID);
}

/** Celdas de fecha (Date de Sheets) -> texto 'yyyy-MM-dd HH:mm' en la zona horaria de la hoja; el resto, tal cual. */
function plainValues_(values, tz) {
  return values.map(function (row) {
    return row.map(function (c) {
      return Object.prototype.toString.call(c) === '[object Date]' && !isNaN(c.getTime()) ? Utilities.formatDate(c, tz, 'yyyy-MM-dd HH:mm') : c;
    });
  });
}

/* =====================================================================================================
 * 2) CÁLCULO PURO — portable (Node, TypeScript…). Aquí no se usa SpreadsheetApp, HtmlService, Utilities,
 *    Session, document ni window. Entrada: { 'Nombre de pestaña': [[celdas planas]] }; salida: objeto JSON.
 * ===================================================================================================== */

/**
 * tabs: { 'Nombre de pestaña': matriz de valores }.  opts: { tz, now, today }.
 * now: Date o texto ISO (solo para «calculado el…»); today: 'yyyy-MM-dd' del día en la zona de la hoja
 * (si falta, se deduce de now con la hora local del entorno). Se puede ejecutar tal cual desde harness.js.
 */
function computeDashboard_(tabs, opts) {
  opts = opts || {};
  var tz = opts.tz || 'Europe/Madrid';
  var now = opts.now || new Date();
  var today = opts.today || toKey_(now);
  var warnings = [], status = [];
  var T = resolveTabs_(tabs || {});

  var H = readHistorico_(T.historico, warnings, status);
  var P = readPositions_(T, warnings, status);
  var R = readFondosRef_(T.fondosRef, warnings, status);
  var G = readTargets_(T.targets, warnings, status);
  var F = readFlujos_(T.flujos, warnings, status);
  var C = readCrypto_(T.crypto, warnings, status);
  var M = readCryptoMovs_(T.cryptoMovs, warnings, status);

  var history = H.history, lastHist = history.length ? history[history.length - 1] : null;
  var crDays = C.days, lastCr = crDays.length ? crDays[crDays.length - 1] : null;
  var lastPosDate = P.dates.length ? P.dates[P.dates.length - 1] : null;
  // Fin de la ventana común de todos los periodos: el último dato de patrimonio (MyInvestor o cripto)
  var asOf = maxKey_([lastHist ? lastHist.date : null, lastCr ? lastCr.date : null]) || lastPosDate;

  // ---------- Flujos de MyInvestor (Flujos + columna de aportaciones de Historico, sin duplicar) ----------
  var miFlows = mergeFlows_(F.flows, history, H.hasAport);
  var aportacionesBBVA = catalogoAportaciones_(miFlows);
  var flowsKnown = F.found || H.hasAport;

  // ---------- Cadenas de rentabilidad (TWR) ----------
  var miChain = twrChain_(history.map(function (h) { return { date: h.date, value: h.total }; }),
    miFlows.map(function (f) { return { date: f.date, amount: f.importe }; }));
  var mainAsset = cryptoMainAsset_(lastCr);
  var movs = summarizeMovs_(M, mainAsset);
  var crFlows = cryptoFlows_(crDays, movs, mainAsset);
  var crChain = twrChain_(crDays.map(function (d) { return { date: d.date, value: d.valor }; }), crFlows);

  // ---------- Serie del patrimonio total (unión de fechas, forward-fill de cada bloque) ----------
  var series = totalSeries_(history, crDays);
  var totalFlows = [];
  var firstTotal = series.length ? series[0].d : null;
  miFlows.forEach(function (f) {
    if (!history.length || f.date <= history[0].date) return;
    var i = firstIndexGE_(history, f.date);
    if (i > 0) totalFlows.push({ date: f.date, eff: history[i].date, amount: f.importe });
  });
  crFlows.forEach(function (f) { totalFlows.push({ date: f.date, eff: f.eff || f.date, amount: f.amount }); });
  // Un bloque que empieza a registrarse más tarde entra como aportación por su primer valor (no es ganancia)
  if (history.length && history[0].date > firstTotal) totalFlows.push({ date: history[0].date, eff: history[0].date, amount: history[0].total });
  if (crDays.length && crDays[0].date > firstTotal) totalFlows.push({ date: crDays[0].date, eff: crDays[0].date, amount: crDays[0].valor });
  var totalChain = twrChain_(series.map(function (p) { return { date: p.d, value: p.t }; }), totalFlows);

  // ---------- Benchmark (proxy: fondo indexado de la propia cartera) ----------
  var bench = buildBenchmark_(G.benchmark, R.ref, P);
  var benchChain = twrChain_(bench.points, []);

  // ---------- Periodos ----------
  var periods = asOf ? periodDefs_(asOf).map(function (def) {
    var p = {
      key: def.key, label: def.label, start: def.start,
      total: periodStats_(totalChain, def.start, asOf),
      mi: periodStats_(miChain, def.start, asOf),
      cr: periodStats_(crChain, def.start, asOf),
      bench: periodStats_(benchChain, def.start, asOf)
    };
    // Diferencia MyInvestor − índice, solo si las dos series cubren exactamente las mismas fechas
    p.benchDiff = p.mi && p.bench && p.mi.twr !== null && p.bench.twr !== null && p.mi.from === p.bench.from && p.mi.to === p.bench.to ? p.mi.twr - p.bench.twr : null;
    return p;
  }) : [];

  // ---------- Índices acumulados (para los gráficos de rentabilidad) ----------
  var index = indexSeries_({ total: totalChain, mi: miChain, cr: crChain, bench: benchChain });
  var ranges = rangeStarts_(asOf);

  // ---------- Bloques ----------
  var mi = buildMyInvestor_({
    H: H, P: P, R: R, G: G, asOf: asOf, miFlows: miFlows, miChain: miChain, flowsKnown: flowsKnown,
    flujosFound: F.found, warnings: warnings
  });
  var miTotal = mi.totals.patrimonio;
  var crypto = buildCrypto_(C, movs, mainAsset, crChain, crFlows);
  var crTotal = crypto.available ? crypto.valor : null;
  var patrimonio = (miTotal === null && crTotal === null) ? null : (miTotal || 0) + (crTotal || 0);
  if (crypto.available) crypto.pesoTotal = patrimonio ? crypto.valor / patrimonio : null;

  // ---------- v4: rendimiento diario (solo mercado), drivers y base 100 ----------
  var daily = buildDaily_({ P: P, ref: R.ref, history: history, miChain: miChain, crDays: crDays, crChain: crChain,
    mainAsset: mainAsset, totalChain: totalChain, asOf: asOf });

  var perfNotes = [];
  if (!flowsKnown && history.length) perfNotes.push('No hay pestaña Flujos ni columna de aportaciones en Historico: se asume que no hubo aportaciones ni retiradas.');
  if (crDays.length && history.length && crDays[0].date > history[0].date) perfNotes.push('La cripto se registra desde el ' + crDays[0].date + ': al entrar en la serie cuenta como aportación, no como ganancia.');
  if (history.length && crDays.length && history[0].date > crDays[0].date) perfNotes.push('MyInvestor se registra desde el ' + history[0].date + ': al entrar en la serie cuenta como aportación, no como ganancia.');

  return sanitize_({
    meta: {
      generatedAt: toIso_(now), tz: tz, today: today, asOf: asOf, ranges: ranges,
      miDaysOld: lastHist && today ? daysBetween_(lastHist.date, today) : null,
      crDaysOld: lastCr && today ? daysBetween_(lastCr.date, today) : null
    },
    total: {
      available: patrimonio !== null, patrimonio: patrimonio,
      myinvestor: miTotal, cripto: crTotal,
      hasMyInvestor: miTotal !== null, hasCripto: crTotal !== null,
      pesoMyInvestor: patrimonio && miTotal !== null ? miTotal / patrimonio : null,
      pesoCripto: patrimonio && crTotal !== null ? crTotal / patrimonio : null,
      miDate: mi.asOf, miSource: mi.source.lastSource, miSourceLabel: mi.source.lastSourceLabel,
      crDate: lastCr ? lastCr.date : null, crPriceTime: lastCr ? lastCr.priceTime : '',
      last: lastChange_(totalChain)
    },
    series: series,
    flujosPatrimonio: wealthFlows_(miChain, flowsKnown, crChain, crDays, crFlows),
    perf: { asOf: asOf, periods: periods, index: index, bench: benchRanges_(index, ranges, bench.info.available), notes: perfNotes, flowsKnown: flowsKnown },
    benchmark: bench.info,
    mi: mi,
    conciliacionBBVA: { version: 1, aportaciones: aportacionesBBVA },
    crypto: crypto,
    daily: daily,
    status: status,
    warnings: warnings
  });
}

/* ===================== Lectura de pestañas ===================== */

function resolveTabs_(tabs) {
  var byKey = {};
  Object.keys(tabs).forEach(function (name) { byKey[tabKey_(name)] = { name: name, values: tabs[name] || [] }; });
  var out = {};
  Object.keys(TAB_ALIASES).forEach(function (k) {
    out[k] = null;
    TAB_ALIASES[k].forEach(function (alias) { if (!out[k] && byKey[tabKey_(alias)]) out[k] = byKey[tabKey_(alias)]; });
  });
  return out;
}

function isBlankTab_(tab) {
  return !tab.values || !tab.values.some(function (r) { return r && r.some(function (c) { return c !== '' && c !== null && c !== undefined; }); });
}

/** Estado de lectura de una pestaña (se muestra en «Estado de los datos»). */
function tabStatus_(label, tab, optional) {
  var st = { tab: label, found: !!tab, rows: 0, from: null, to: null, level: 'ok', msg: '' };
  if (!tab) { st.level = optional ? 'info' : 'warn'; st.msg = 'No existe la pestaña'; }
  else if (isBlankTab_(tab)) { st.level = optional ? 'info' : 'warn'; st.msg = 'Pestaña vacía'; }
  return st;
}
function statusRange_(st, items) {
  st.rows = items.length;
  items.forEach(function (it) {
    if (!it.date) return;
    if (!st.from || it.date < st.from) st.from = it.date;
    if (!st.to || it.date > st.to) st.to = it.date;
  });
}

function readHistorico_(tab, warnings, status) {
  var out = { rows: [], history: [], hasAport: false };
  var st = tabStatus_('Historico', tab, false); status.push(st);
  if (st.msg) { warnings.push('Historico: ' + st.msg.toLowerCase() + ' — sin ella no hay Total, efectivo ni evolución de MyInvestor.'); return out; }
  var t = findTable_(tab.values, SPEC.historico, ['fecha', ['total', 'inversiones', 'fondos']]);
  if (!t) {
    st.level = 'warn'; st.msg = 'No encuentro la cabecera (Fecha y Total)';
    warnings.push('Historico: no encuentro una cabecera con Fecha y Total.');
    return out;
  }
  out.hasAport = t.has('aport');
  t.rows.forEach(function (r, i) {
    var d = toKey_(t.get(r, 'fecha')); if (!d) return;
    var fondos = num_(t.get(r, 'fondos')), acciones = num_(t.get(r, 'acciones')), efectivo = num_(t.get(r, 'efectivo'));
    var inv = num_(t.get(r, 'inversiones')), total = num_(t.get(r, 'total'));
    if (inv === null && (fondos !== null || acciones !== null)) inv = (fondos || 0) + (acciones || 0);
    if (total === null && inv !== null) total = inv + (efectivo || 0);
    if (total === null || (total === 0 && !inv && !efectivo)) return;   // filas futuras o con fórmulas aún a cero
    out.rows.push({ date: d, ord: i, source: normSource_(t.get(r, 'source')), inversiones: inv, fondos: fondos, acciones: acciones,
      efectivo: efectivo, total: total, aport: num_(t.get(r, 'aport')), nota: str_(t.get(r, 'nota')) });
  });
  // Un punto por fecha: gana broker; si no, la última fila del día. La aportación del día no se pierde.
  var by = {};
  out.rows.forEach(function (r) {
    var cur = by[r.date];
    if (!cur) { by[r.date] = r; return; }
    var aport = Math.abs(r.aport || 0) > 0.005 ? r.aport : cur.aport;
    if (r.source === 'broker' || cur.source !== 'broker') by[r.date] = r;
    by[r.date] = copy_(by[r.date]); by[r.date].aport = aport;
  });
  out.history = Object.keys(by).map(function (k) { return by[k]; }).sort(byDate_);
  statusRange_(st, out.history);
  if (!out.history.length) { st.level = 'warn'; st.msg = 'Sin filas con fecha y total'; warnings.push('Historico: no hay filas con fecha y total.'); }
  return out;
}

/** Posiciones: admite la pestaña combinada «Stocks y Fondos», las separadas Stocks + Fondos, o ambas a la vez. */
function readPositions_(T, warnings, status) {
  var rows = [], ord = 0, anyTab = false;
  [['stocks', 'stock', 'Stocks'], ['fondos', 'fondo', 'Fondos'], ['posiciones', null, 'Stocks y Fondos']].forEach(function (cfg) {
    var tab = T[cfg[0]], st = tabStatus_(cfg[2], tab, true); status.push(st);
    if (st.msg) return;
    anyTab = true;
    var t = findTable_(tab.values, SPEC.posiciones, ['fecha', 'id', 'valor']);
    if (!t) {
      st.level = 'warn'; st.msg = 'No encuentro la cabecera (Fecha, ISIN/Ticker y Valor)';
      warnings.push(cfg[2] + ': no encuentro una cabecera con Fecha, ISIN_Ticker y Valor.');
      return;
    }
    var mine = [];
    t.rows.forEach(function (r) {
      var d = toKey_(t.get(r, 'fecha')); if (!d) return;
      var id = str_(t.get(r, 'id')); if (!id) return;
      var valor = num_(t.get(r, 'valor')), cantidad = num_(t.get(r, 'cantidad'));
      if (valor === null || (valor === 0 && !cantidad)) return;   // posición ya vendida o fila sin rellenar
      var kind = kindOf_(t.get(r, 'tipo'), cfg[1], id);
      mine.push({
        date: d, ord: ord++, source: normSource_(t.get(r, 'source')), kind: kind, id: id,
        nombre: str_(t.get(r, 'nombre')) || id, cuenta: str_(t.get(r, 'cuenta')), valor: valor,
        cantidad: cantidad, nav: num_(t.get(r, 'nav')), navDate: toKey_(t.get(r, 'navDate')),
        mercado: str_(t.get(r, 'mercado')), coste: num_(t.get(r, 'coste')), plusvalia: num_(t.get(r, 'plusvalia')),
        nota: str_(t.get(r, 'nota'))
      });
    });
    statusRange_(st, mine);
    rows = rows.concat(mine);
  });
  // Si conviven las dos variantes no se duplica nada: la pestaña combinada va al final y gana (ISIN + cuenta + fecha).
  var byDate = {};
  rows.forEach(function (r) { (byDate[r.date] || (byDate[r.date] = [])).push(r); });
  if (!rows.length) warnings.push(anyTab ? 'Posiciones: las pestañas de posiciones no tienen filas válidas.' : 'Posiciones: no existe «Stocks y Fondos» ni Stocks/Fondos — sin detalle de cartera.');
  else status.forEach(function (st) { if (!st.found && /^(Stocks|Fondos|Stocks y Fondos)$/.test(st.tab)) st.msg = 'No existe (no hace falta: las posiciones salen de la otra variante)'; });
  return { rows: rows, byDate: byDate, dates: Object.keys(byDate).sort() };
}

function kindOf_(tipo, def, id) {
  var t = norm_(tipo);
  if (/^(fondo|fund|fi_|fi$|plan)/.test(t)) return 'fondo';
  if (/^(stock|accion|etf|etc|equity|share|renta_variable_directa)/.test(t)) return 'stock';
  if (def) return def;
  return /^[A-Z]{2}[A-Z0-9]{9}[0-9]$/.test(String(id).toUpperCase()) ? 'fondo' : 'stock';
}

/** Posiciones de una fecha: si hay filas broker ese día se usan solo esas; dedup ISIN+cuenta (la última gana). */
function snapshot_(P, date) {
  var day = (date && P.byDate[date]) || [];
  var hasBroker = day.some(function (r) { return r.source === 'broker'; });
  var use = hasBroker ? day.filter(function (r) { return r.source === 'broker'; }) : day;
  var map = {}, order = [];
  use.forEach(function (r) { var k = r.id + '|' + r.cuenta; if (!(k in map)) order.push(k); map[k] = r; });
  return { date: date, source: hasBroker ? 'broker' : (use[0] ? use[0].source : null), rows: order.map(function (k) { return map[k]; }) };
}

function readFondosRef_(tab, warnings, status) {
  var out = { ref: {}, geoKeys: [], found: false };
  var st = tabStatus_('Fondos_Ref', tab, true); status.push(st);
  if (st.msg) { st.msg += ' (sin TER, clase ni geografía de fondos)'; return out; }
  var t = findTable_(tab.values, SPEC.fondosRef, ['isin']);
  if (!t) { st.level = 'warn'; st.msg = 'No encuentro la cabecera (ISIN)'; warnings.push('Fondos_Ref: no encuentro la cabecera con ISIN (TER y geografía no disponibles).'); return out; }
  out.found = true;
  var geoCols = [];
  t.keys.forEach(function (k, c) { var m = k.match(/^geo_([a-z]+)$/); if (m) geoCols.push({ key: m[1].toUpperCase(), col: c }); });
  out.geoKeys = geoCols.map(function (g) { return g.key; });
  var n = 0;
  t.rows.forEach(function (r) {
    var isin = str_(t.get(r, 'isin')); if (!isin) return;
    var geo = {}; geoCols.forEach(function (g) { geo[g.key] = pct_(r[g.col]); });
    out.ref[isin] = {
      nombre: str_(t.get(r, 'nombre')), ter: pct_(t.get(r, 'ter'), true), clase: str_(t.get(r, 'clase')).toUpperCase(),
      rv: pct_(t.get(r, 'rv')), rf: pct_(t.get(r, 'rf')), geo: geo, estilo: str_(t.get(r, 'estilo')),
      gestora: str_(t.get(r, 'gestora')), benchmark: str_(t.get(r, 'benchmark')), divisa: str_(t.get(r, 'divisa')),
      actualizado: toKey_(t.get(r, 'actualizado'))
    };
    n++;
  });
  st.rows = n;
  return out;
}

function readTargets_(tab, warnings, status) {
  var out = { targets: { cash: DEFAULT_TARGETS.cash, rv: DEFAULT_TARGETS.rv, rf: DEFAULT_TARGETS.rf, fromSheet: false }, benchmark: DEFAULT_BENCHMARK, benchmarkFromSheet: false };
  var st = tabStatus_('Targets', tab, true); status.push(st);
  if (st.msg) { st.msg += ' (uso objetivo 5 / 80 / 15 y benchmark MSCI World)'; return out; }
  tab.values.forEach(function (r) {
    if (!r || hkey_(r[0]) !== 'benchmark') return;
    for (var c = 1; c < r.length; c++) if (str_(r[c])) { out.benchmark = str_(r[c]); out.benchmarkFromSheet = true; break; }
  });
  var t = findTable_(tab.values, SPEC.targets, ['categoria', 'target']);
  if (!t) { st.level = 'info'; st.msg = 'Sin tabla Categoria / Target_% (uso 5 / 80 / 15)'; return out; }
  var n = 0;
  t.rows.forEach(function (r) {
    var cat = hkey_(t.get(r, 'categoria')), v = pct_(t.get(r, 'target'));
    if (v === null || !cat) return;
    var key = /^(cash|efectivo|liquidez)/.test(cat) ? 'cash' : /^(rv|renta_variable)/.test(cat) ? 'rv' : /^(rf|renta_fija)/.test(cat) ? 'rf' : null;
    if (!key) return;
    out.targets[key] = v; out.targets.fromSheet = true; n++;
  });
  st.rows = n;
  if (!n) st.msg = 'Sin objetivos numéricos (uso 5 / 80 / 15)';
  return out;
}

/** Flujos: solo cuentan aportaciones (+) y retiradas (−); las filas tipo «nota» se ignoran. */
function readFlujos_(tab, warnings, status) {
  var out = { flows: [], found: false, notas: 0 };
  var st = tabStatus_('Flujos', tab, true); status.push(st);
  if (st.msg) { st.msg += ' (se asume que no hubo aportaciones ni retiradas)'; return out; }
  var t = findTable_(tab.values, SPEC.flujos, ['fecha', 'tipo', 'importe']);
  if (!t) { st.level = 'warn'; st.msg = 'No encuentro la cabecera (Fecha, Tipo, Importe)'; warnings.push('Flujos: no encuentro la cabecera Fecha / Tipo / Importe.'); return out; }
  out.found = true;
  t.rows.forEach(function (r) {
    var d = toKey_(t.get(r, 'fecha')); if (!d) return;
    var tipo = norm_(t.get(r, 'tipo'));
    var sign = /^(aport|ingres|deposit|entrada)/.test(tipo) ? 1 : /^(retir|reembols|salida)/.test(tipo) ? -1 : 0;
    if (!sign) { out.notas++; return; }
    var imp = Math.abs(num_(t.get(r, 'importe')) || 0);
    if (imp < 0.005) return;
    out.flows.push({ date: d, tipo: sign > 0 ? 'aportación' : 'retirada', importe: sign * imp, nota: str_(t.get(r, 'nota')), origen: 'Flujos' });
  });
  out.flows.sort(byDate_);
  statusRange_(st, out.flows);
  st.msg = out.flows.length ? '' : 'Sin aportaciones ni retiradas registradas' + (out.notas ? ' (' + out.notas + ' filas de nota)' : '');
  return out;
}

/**
 * Une Flujos con la columna de aportaciones de Historico sin contar dos veces:
 * una aportación de Historico se da por registrada si Flujos tiene el mismo importe (±0,50 €)
 * entre 5 días antes y 1 día después.
 */
function mergeFlows_(flujos, history, hasAport) {
  var out = flujos.map(copy_), used = {};
  if (hasAport) history.forEach(function (h) {
    if (!h.aport || Math.abs(h.aport) < 0.005) return;
    var lo = shiftDays_(h.date, -5), hi = shiftDays_(h.date, 1), cands = [];
    flujos.forEach(function (f, i) { if (!used[i] && f.date >= lo && f.date <= hi) cands.push(i); });
    var one = -1, sum = 0;
    cands.forEach(function (i) { sum += flujos[i].importe; if (one < 0 && Math.abs(flujos[i].importe - h.aport) < 0.5) one = i; });
    if (one >= 0) { used[one] = true; return; }
    if (cands.length && Math.abs(sum - h.aport) < 0.5) { cands.forEach(function (i) { used[i] = true; }); return; }
    out.push({ date: h.date, tipo: h.aport > 0 ? 'aportación' : 'retirada', importe: h.aport, nota: 'Columna de aportaciones de Historico', origen: 'Historico' });
  });
  return out.sort(byDate_);
}

/** Catálogo completo de los flujos positivos que usa el cálculo, no de la tabla recortada. */
function catalogoAportaciones_(flows) {
  var groups = Object.create(null), aportaciones = [];
  flows.forEach(function (f) {
    var cents = Math.round(f.importe * 100);
    if (cents <= 0 || !Number.isSafeInteger(cents)) return;
    var key = JSON.stringify([f.date, cents, f.origen, f.nota]);
    (groups[key] || (groups[key] = [])).push(f);
  });
  Object.keys(groups).forEach(function (key) {
    groups[key].forEach(function (f, index, group) {
      // Si cambia el número de filas indistinguibles no se reasigna un enlace antiguo.
      f.claveBBVA = JSON.stringify([key, group.length, index]);
      aportaciones.push({ clave: f.claveBBVA, fecha: f.date, centimos: Math.round(f.importe * 100),
        origen: f.origen, nota: f.nota });
    });
  });
  return aportaciones;
}

/** Crypto: un punto por día (suma de activos/plataformas; por activo+plataforma gana broker o la última fila). */
function readCrypto_(tab, warnings, status) {
  var out = { available: false, reason: '', days: [] };
  var st = tabStatus_('Crypto', tab, true); status.push(st);
  if (st.msg) { out.reason = tab ? 'La pestaña Crypto está vacía.' : 'No existe la pestaña Crypto.'; st.msg += ' (el patrimonio total es solo MyInvestor)'; return out; }
  var t = findTable_(tab.values, SPEC.crypto, ['fecha', ['valor', 'cantidad']]);
  if (!t) {
    st.level = 'warn'; st.msg = 'No encuentro la cabecera (Fecha y Valor)';
    out.reason = 'La pestaña Crypto no tiene una cabecera con Fecha y Valor_EUR.';
    warnings.push('Crypto: no encuentro una cabecera con Fecha y Valor_EUR.');
    return out;
  }
  var byDate = {};
  t.rows.forEach(function (r, i) {
    var d = toKey_(t.get(r, 'fecha')); if (!d) return;
    var qty = num_(t.get(r, 'cantidad')), precio = num_(t.get(r, 'precio')), v = num_(t.get(r, 'valor'));
    if (v === null && qty !== null && precio !== null) v = qty * precio;
    if (v === null || (v === 0 && !qty)) return;   // fila sin rellenar
    if (precio === null && qty) precio = v / qty;
    var row = { date: d, ord: i, source: normSource_(t.get(r, 'source')), activo: str_(t.get(r, 'activo')).toUpperCase() || 'BTC',
      plataforma: str_(t.get(r, 'plataforma')), cantidad: qty, precio: precio, valor: v, coste: num_(t.get(r, 'coste')),
      priceTime: timeLabel_(t.get(r, 'priceTime')), nota: str_(t.get(r, 'nota')) };
    var day = byDate[d] || (byDate[d] = {}), k = row.activo + '|' + row.plataforma.toLowerCase(), cur = day[k];
    if (!cur || row.source === 'broker' || cur.source !== 'broker') day[k] = row;
  });
  out.days = Object.keys(byDate).sort().map(function (d) {
    var items = Object.keys(byDate[d]).map(function (k) { return byDate[d][k]; }).sort(function (a, b) { return a.ord - b.ord; });
    var valor = 0, coste = null, lastItem = items[items.length - 1];
    items.forEach(function (it) { valor += it.valor; if (it.coste !== null) coste = (coste || 0) + it.coste; });
    return { date: d, valor: valor, coste: coste, items: items, priceTime: lastItem.priceTime, nota: lastItem.nota,
      source: items.every(function (it) { return it.source === 'broker'; }) ? 'broker' : items[items.length - 1].source };
  });
  statusRange_(st, out.days);
  if (!out.days.length) { st.level = 'info'; st.msg = 'Sin filas con fecha y valor'; out.reason = 'La pestaña Crypto aún no tiene filas con fecha y valor.'; return out; }
  out.available = true;
  return out;
}

function readCryptoMovs_(tab, warnings, status) {
  var out = { found: false, reason: '', rows: [], hasAcum: false };
  var st = tabStatus_('Crypto_Movs', tab, true); status.push(st);
  if (st.msg) { out.reason = tab ? 'La pestaña Crypto_Movs está vacía.' : 'No existe la pestaña Crypto_Movs.'; st.msg += ' (sin detalle de compras)'; return out; }
  var t = findTable_(tab.values, SPEC.cryptoMovs, ['fecha', 'cantidad']);
  if (!t) {
    st.level = 'warn'; st.msg = 'No encuentro la cabecera (Fecha y Cantidad_BTC)';
    out.reason = 'La pestaña Crypto_Movs no tiene una cabecera con Fecha y Cantidad_BTC.';
    warnings.push('Crypto_Movs: no encuentro una cabecera con Fecha y Cantidad_BTC.');
    return out;
  }
  out.found = true; out.hasAcum = t.has('acum');
  t.rows.forEach(function (r, i) {
    var d = toKey_(t.get(r, 'fecha')); if (!d) return;
    var qty = num_(t.get(r, 'cantidad')); if (qty === null || !qty) return;
    var tipo = norm_(t.get(r, 'tipo'));
    var side = /^(compra|buy|c$|bid)/.test(tipo) ? 'compra' : /^(venta|sell|v$|ask)/.test(tipo) ? 'venta'
      : /^(retir|salida|withdraw|envio)/.test(tipo) ? 'salida' : /^(deposit|entrada|ingres|transfer|recib|earn|staking|regalo|saldo)/.test(tipo) ? 'entrada'
      : (qty < 0 ? 'venta' : 'compra');
    out.rows.push({ date: d, ord: i, par: str_(t.get(r, 'par')).toUpperCase(), tipo: side, cantidad: Math.abs(qty),
      acum: num_(t.get(r, 'acum')), precio: num_(t.get(r, 'precio')), total: num_(t.get(r, 'total')),
      comision: Math.abs(num_(t.get(r, 'comision')) || 0) });
  });
  out.rows.sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : a.ord - b.ord; });
  statusRange_(st, out.rows);
  if (!out.rows.length) { st.msg = 'Sin movimientos'; out.reason = 'La pestaña Crypto_Movs aún no tiene movimientos.'; }
  return out;
}

/* ===================== Cripto ===================== */

function cryptoMainAsset_(day) {
  if (!day) return 'BTC';
  var by = {};
  day.items.forEach(function (it) { by[it.activo] = (by[it.activo] || 0) + it.valor; });
  return Object.keys(by).sort(function (a, b) { return by[b] - by[a]; })[0] || 'BTC';
}

function assetAgg_(day, activo) {
  var q = 0, v = 0, hasQ = false, px = null, n = 0, plats = [];
  day.items.forEach(function (it) {
    if (it.activo !== activo) return;
    n++; v += it.valor; if (it.cantidad !== null) { q += it.cantidad; hasQ = true; }
    if (it.precio !== null) px = it.precio;
    if (it.plataforma && plats.indexOf(it.plataforma) < 0) plats.push(it.plataforma);
  });
  // Con una sola fila manda el Precio_EUR de la hoja; con varias plataformas, el precio medio ponderado
  return { cantidad: hasQ ? q : null, valor: v, precio: (n === 1 && px !== null) ? px : (hasQ && q ? v / q : px), plataformas: plats };
}

/**
 * Resume Crypto_Movs: BTC comprados con coste, coste en EUR (coste medio ponderado si hay ventas),
 * precio medio y comisiones. Deduce de Cantidad_acum_BTC si la comisión en BTC ya está descontada.
 */
function summarizeMovs_(M, activo) {
  var out = { available: false, reason: M.reason, items: [], timeline: [], nCompras: 0, nVentas: 0, btcConCoste: 0, coste: 0,
    eurComprado: 0, btcComprado: 0, precioMedio: null, comisionBtc: 0, btcSinCosteMovs: 0, realizado: 0, first: null, last: null, restaComision: true };
  if (!M.found) return out;
  var alt = activo === 'BTC' ? 'XBT' : activo;
  var rows = M.rows.filter(function (r) { return !r.par || r.par.indexOf(activo) >= 0 || r.par.indexOf(alt) >= 0; });
  if (!rows.length) { out.reason = out.reason || ('Crypto_Movs no tiene movimientos de ' + activo + '.'); return out; }
  // ¿Cantidad_acum suma la cantidad bruta o la neta de comisión?
  var votesNet = 0, votesGross = 0;
  rows.forEach(function (r, i) {
    if (!r.comision || r.acum === null) return;
    var prev = i > 0 ? rows[i - 1].acum : 0; if (prev === null) return;
    var delta = Math.abs(r.acum - prev);
    if (Math.abs(delta - (r.cantidad - r.comision)) < Math.abs(delta - r.cantidad)) votesNet++; else votesGross++;
  });
  out.restaComision = votesNet + votesGross === 0 ? true : votesNet >= votesGross;

  var qty = 0, cost = 0;
  rows.forEach(function (r) {
    var inflow = r.tipo === 'compra' || r.tipo === 'entrada';
    var neto = inflow ? r.cantidad - (out.restaComision ? r.comision : 0) : r.cantidad + (out.restaComision ? r.comision : 0);
    var eur = r.total !== null ? Math.abs(r.total) : (r.precio !== null ? r.cantidad * r.precio : null);
    out.comisionBtc += r.comision;
    if (inflow) {
      if (eur !== null && eur > 0.005) {
        cost += eur; qty += neto;
        if (r.tipo === 'compra') { out.nCompras++; out.eurComprado += eur; out.btcComprado += neto; }
      } else out.btcSinCosteMovs += neto;
    } else {
      var avg = qty > 1e-12 ? cost / qty : 0, sold = Math.min(neto, qty);
      if (r.tipo === 'venta') { out.nVentas++; if (eur !== null) out.realizado += eur - avg * sold; }
      cost -= avg * sold; qty -= sold;
    }
    out.timeline.push({ date: r.date, qty: qty, cost: cost });
    out.items.push({ date: r.date, par: r.par, tipo: r.tipo, cantidad: r.cantidad, neto: (inflow ? 1 : -1) * neto, precio: r.precio !== null ? r.precio : (eur !== null && r.cantidad ? eur / r.cantidad : null),
      total: eur, comision: r.comision, acum: r.acum });
  });
  out.available = true;
  out.btcConCoste = Math.max(0, qty); out.coste = Math.max(0, cost);
  out.precioMedio = out.btcConCoste > 1e-12 ? out.coste / out.btcConCoste : null;
  out.first = rows[0].date; out.last = rows[rows.length - 1].date;
  return out;
}

/** Coste y BTC con coste según Crypto_Movs a una fecha dada. */
function movsAt_(movs, date) {
  var res = null;
  movs.timeline.forEach(function (p) { if (p.date <= date) res = p; });
  return res;
}

/**
 * Flujos de la cripto (dinero que entra o sale) entre valoraciones consecutivas de la pestaña Crypto.
 * Si los movimientos de Crypto_Movs explican el cambio de cantidad se usa su importe real en EUR;
 * si no, el flujo se deduce del cambio de cantidad valorado al precio del día.
 */
function cryptoFlows_(days, movs, activo) {
  var flows = [];
  for (var i = 1; i < days.length; i++) {
    var prev = days[i - 1], cur = days[i], keys = {}, inferred = [], dMain = 0, eurMain = 0;
    prev.items.forEach(function (it) { keys[it.activo + '|' + it.plataforma.toLowerCase()] = { p: it, c: null }; });
    cur.items.forEach(function (it) { var k = it.activo + '|' + it.plataforma.toLowerCase(); (keys[k] || (keys[k] = { p: null, c: null })).c = it; });
    Object.keys(keys).forEach(function (k) {
      var e = keys[k], it = e.c || e.p, eur;
      if (e.p && e.c) {
        if (e.p.cantidad === null || e.c.cantidad === null) return;
        var dq = e.c.cantidad - e.p.cantidad, px = e.c.precio !== null ? e.c.precio : e.p.precio;
        if (Math.abs(dq) < 1e-9 || px === null) return;
        eur = dq * px;
        if (it.activo === activo) dMain += dq;
      } else {
        eur = e.c ? e.c.valor : -e.p.valor;     // activo o plataforma que aparece / desaparece
        if (it.activo === activo && it.cantidad !== null) dMain += e.c ? it.cantidad : -it.cantidad;
      }
      if (it.activo === activo) eurMain += eur; else inferred.push({ date: cur.date, eff: cur.date, amount: eur, origen: 'deducido' });
    });
    var inWin = movs.available ? movs.items.filter(function (m) { return m.date > prev.date && m.date <= cur.date; }) : [];
    var qMov = inWin.reduce(function (s, m) { return s + m.neto; }, 0);
    if (inWin.length && Math.abs(qMov - dMain) <= Math.max(2e-7, 0.02 * Math.abs(dMain))) {
      var px2 = assetAgg_(cur, activo).precio;
      inWin.forEach(function (m) {
        var eur2 = m.total !== null ? m.total : (px2 !== null ? Math.abs(m.neto) * px2 : 0);
        flows.push({ date: m.date, eff: cur.date, amount: (m.neto >= 0 ? 1 : -1) * eur2, origen: 'Crypto_Movs', estimado: m.total === null });
      });
    } else if (Math.abs(eurMain) > 0.005) {
      flows.push({ date: cur.date, eff: cur.date, amount: eurMain, origen: 'deducido' });
    }
    flows = flows.concat(inferred.filter(function (f) { return Math.abs(f.amount) > 0.005; }));
  }
  return flows;
}

/** Flujos utilizados por el cálculo de mercado, agrupados por fecha de valoración. */
function wealthFlows_(miChain, flowsKnown, crChain, crDays, crFlows) {
  var estimated = {};
  crFlows.forEach(function (flow) {
    if (flow.origen === 'deducido' || flow.estimado) estimated[flow.eff || flow.date] = true;
  });
  var missingQuantity = crDays.map(function (day) {
    return day.items.some(function (item) { return item.cantidad === null; });
  });
  return {
    version: 1,
    myinvestor: flowsKnown ? miChain.map(function (point) {
      return { fecha: point.date, neto: point.flow };
    }) : null,
    cripto: crChain.map(function (point, i) {
      var unknown = i > 0 && (missingQuantity[i - 1] || missingQuantity[i]);
      return { fecha: point.date, neto: unknown ? null : point.flow, estimado: Boolean(estimated[point.date]) };
    })
  };
}

function buildCrypto_(C, movs, activo, chain, flows) {
  var movOut = {
    available: movs.available, reason: movs.reason, items: movs.items.slice().reverse().slice(0, 60), nMovs: movs.items.length,
    nCompras: movs.nCompras, nVentas: movs.nVentas, eurComprado: movs.eurComprado, btcComprado: movs.btcComprado,
    btcConCoste: movs.btcConCoste, coste: movs.coste, precioMedio: movs.precioMedio, comisionBtc: movs.comisionBtc,
    realizado: movs.realizado, first: movs.first, last: movs.last, comisionEur: null, precioVsMedio: null
  };
  if (!C.available) return { available: false, reason: C.reason, movs: movOut };
  var days = C.days, last = days[days.length - 1], agg = assetAgg_(last, activo);
  // Comisiones al precio actual y precio actual frente al medio de compra
  if (agg.precio !== null) {
    movOut.comisionEur = movs.comisionBtc * agg.precio;
    movOut.precioVsMedio = movs.precioMedio ? agg.precio / movs.precioMedio - 1 : null;
  }

  // Coste: valoración del día o historial de compras; desconocido si faltan ambos.
  var coste = null, costeOrigen = null, costeFecha = null;
  var registro = movsAt_(movs, last.date);
  if (last.coste !== null) { coste = last.coste; costeFecha = last.date; costeOrigen = 'hoja'; }
  else if (registro && registro.qty > 0) { coste = registro.cost; costeFecha = registro.date; costeOrigen = 'movs'; }
  var plus = coste !== null ? last.valor - coste : null;

  // BTC sin coste registrado -> la plusvalía latente queda sobreestimada
  var sinCoste = null;
  if (agg.cantidad !== null) {
    if (registro && registro.qty > 0) {
      var sc = agg.cantidad - registro.qty;
      sinCoste = { btc: sc > 1e-7 ? sc : 0, origen: 'movs' };
    }
  }
  var conCoste = null;
  if (sinCoste && sinCoste.btc > 0 && agg.precio !== null && coste !== null) {
    sinCoste.eur = sinCoste.btc * agg.precio;
    var q = agg.cantidad - sinCoste.btc;
    conCoste = { btc: q, precioMedio: q > 1e-12 ? coste / q : null, valor: q * agg.precio, plusvalia: q * agg.precio - coste,
      plusvaliaPct: coste ? (q * agg.precio - coste) / coste : null };
  }

  // Serie diaria: coste declarado para esa valoración o historial de movimientos hasta esa fecha.
  var series = days.map(function (d) {
    var a = assetAgg_(d, activo), c = d.coste;
    if (c === null && movs.available) { var m = movsAt_(movs, d.date); if (m && m.qty > 0) c = m.cost; }
    return { d: d.date, valor: d.valor, precio: a.precio, cantidad: a.cantidad, coste: c, plus: c !== null ? d.valor - c : null, src: d.source };
  });

  var first = series[0], priceChg = {};
  function pxAt(key) { var r = null; series.forEach(function (p) { if (p.d <= key && p.precio !== null) r = p; }); return r; }
  [['d7', shiftDays_(last.date, -7)], ['m1', shiftMonths_(last.date, -1)]].forEach(function (x) {
    var p = pxAt(x[1]);
    priceChg[x[0]] = p && p.precio && agg.precio !== null && p.d !== last.date ? { from: p.d, pct: agg.precio / p.precio - 1 } : null;
  });

  var activos = [];
  if (last.items.length > 1) last.items.forEach(function (it) {
    activos.push({ activo: it.activo, plataforma: it.plataforma, cantidad: it.cantidad, precio: it.precio, valor: it.valor, peso: last.valor ? it.valor / last.valor : null });
  });
  var costeMovsDiff = (costeOrigen === 'hoja' && movs.available && movs.coste > 0 && Math.abs(movs.coste - coste) > 1) ? movs.coste : null;

  return {
    available: true, activo: activo, asOf: last.date, firstDate: first.d, nDias: days.length,
    source: last.source, priceTime: last.priceTime, plataforma: agg.plataformas.join(' + '), nota: last.nota,
    valor: last.valor, valorActivo: agg.valor, cantidad: agg.cantidad, precio: agg.precio,
    coste: coste, costeOrigen: costeOrigen, costeFecha: costeFecha, costeMovsDiff: costeMovsDiff,
    plusvalia: plus, plusvaliaPct: coste ? plus / coste : null,
    sinCoste: sinCoste, conCoste: conCoste,
    pesoTotal: null,
    last: lastChange_(chain), precioCambio: priceChg,
    series: series, activos: activos,
    flujosDeducidos: flows.filter(function (f) { return f.origen === 'deducido'; }).length,
    movs: movOut
  };
}

/* ===================== MyInvestor ===================== */

function buildMyInvestor_(x) {
  var H = x.H, P = x.P, ref = x.R.ref, geoKeys = x.R.geoKeys, targets = x.G.targets;
  var history = H.history, lastHist = history.length ? history[history.length - 1] : null;
  var lastPosDate = P.dates.length ? P.dates[P.dates.length - 1] : null;
  var latest = snapshot_(P, lastPosDate);
  var hasPos = latest.rows.length > 0;

  // Efectivo: el de Historico en la fecha de las posiciones (o el último conocido)
  var cashPoint = null;
  for (var i = history.length - 1; i >= 0; i--) if (history[i].efectivo !== null && (!lastPosDate || history[i].date <= lastPosDate)) { cashPoint = history[i]; break; }
  if (!cashPoint) for (var j = 0; j < history.length; j++) if (history[j].efectivo !== null) { cashPoint = history[j]; break; }
  var cash = cashPoint ? cashPoint.efectivo : 0;
  var cashOfficial = null;
  for (var k = history.length - 1; k >= 0; k--) if (history[k].source === 'broker' && history[k].efectivo !== null) { if (Math.abs(history[k].efectivo - cash) < 0.01) cashOfficial = history[k].date; break; }

  // Variación semanal por posición: foto de posiciones de ~7 días antes del último dato del dashboard
  var weekRefDate = null;
  if (lastPosDate) {
    var target = shiftDays_(x.asOf || lastPosDate, -7);
    P.dates.forEach(function (d) { if (d <= target) weekRefDate = d; });
    if (!weekRefDate && P.dates[0] !== lastPosDate) weekRefDate = P.dates[0];
    if (weekRefDate === lastPosDate) weekRefDate = null;
  }
  var weekMap = {};
  if (weekRefDate) snapshot_(P, weekRefDate).rows.forEach(function (p) { weekMap[p.id + '|' + p.cuenta] = p; });

  var fondosPos = 0, accionesPos = 0, costeTot = 0, plusTot = 0, costeKnown = 0;
  latest.rows.forEach(function (p) {
    if (p.kind === 'fondo') fondosPos += p.valor; else accionesPos += p.valor;
    var pl = p.plusvalia !== null ? p.plusvalia : (p.coste !== null ? p.valor - p.coste : null);
    if (p.coste !== null) { costeTot += p.coste; costeKnown++; }
    if (pl !== null) plusTot += pl;
  });
  var invPos = fondosPos + accionesPos, posTotal = invPos + cash;

  // Cifras de cabecera del bloque: mandan las de Historico (último Total); si faltan, las posiciones
  var patrimonio = lastHist ? lastHist.total : (hasPos ? posTotal : null);
  var fondosEur = lastHist && lastHist.fondos !== null ? lastHist.fondos : (hasPos ? fondosPos : null);
  var accionesEur = lastHist && lastHist.acciones !== null ? lastHist.acciones : (hasPos ? accionesPos : null);
  var efectivoEur = lastHist && lastHist.efectivo !== null ? lastHist.efectivo : (cashPoint ? cash : null);
  var inversionesEur = lastHist && lastHist.inversiones !== null ? lastHist.inversiones : (hasPos ? invPos : null);

  var terWeighted = 0, terBase = 0, annualCost = 0, terMissing = 0;
  var geoTotals = {}, geoMissing = 0, rvEur = 0, rfEur = 0, otrosEur = 0;
  var positions = latest.rows.map(function (p) {
    var r = ref[p.id] || null, v = p.valor;
    var ter = r ? r.ter : (p.kind === 'stock' ? 0 : null);
    var clase = p.kind === 'stock' ? 'RV' : (r && r.clase ? r.clase : (p.id === 'IE00BL1GVT98' ? 'RF' : 'RV'));
    if (p.kind === 'stock') rvEur += v;
    else {
      var rv = r && r.rv !== null ? r.rv : (clase === 'RF' ? 0 : 1);
      var rf = r && r.rf !== null ? r.rf : (clase === 'RF' ? 1 : 0);
      rvEur += v * rv; rfEur += v * rf; otrosEur += v * Math.max(0, 1 - rv - rf);
    }
    if (p.kind === 'fondo') { if (ter !== null) { terWeighted += v * ter; terBase += v; } else terMissing += v; }
    if (ter !== null) annualCost += v * ter;
    if (p.kind === 'stock') {
      var g = STOCK_GEO[p.id.toUpperCase()] || MARKET_GEO[(p.mercado || '').toUpperCase()] || 'OTHER';
      geoTotals[g] = (geoTotals[g] || 0) + v;
    } else {
      var sum = 0; if (r) geoKeys.forEach(function (gk) { sum += r.geo[gk] || 0; });
      if (sum > 0) geoKeys.forEach(function (gk) { if (r.geo[gk]) geoTotals[gk] = (geoTotals[gk] || 0) + v * r.geo[gk] / sum; });
      else { geoTotals.OTHER = (geoTotals.OTHER || 0) + v; geoMissing += v; }
    }
    var w = weekMap[p.id + '|' + p.cuenta];
    var precio = p.nav !== null ? p.nav : (p.cantidad ? v / p.cantidad : null);
    var wPrice = w ? (w.nav !== null ? w.nav : (w.cantidad ? w.valor / w.cantidad : null)) : null;
    var pl = p.plusvalia !== null ? p.plusvalia : (p.coste !== null ? v - p.coste : null);
    return {
      id: p.id, nombre: p.nombre, cuenta: p.cuenta, kind: p.kind, clase: clase, source: p.source,
      valor: v, cantidad: p.cantidad, precio: precio, navDate: p.navDate,
      coste: p.coste, plusvalia: pl, plusPct: (p.coste && pl !== null ? pl / p.coste : null),
      pesoInv: invPos ? v / invPos : null, pesoTotal: posTotal ? v / posTotal : null,
      semanaEur: w && w.valor ? v - w.valor : null, semanaPct: w && w.valor ? v / w.valor - 1 : null,
      semanaPrecioPct: (precio !== null && wPrice) ? precio / wPrice - 1 : null,
      ter: ter, gestora: r ? r.gestora : '', benchmark: r ? r.benchmark : ''
    };
  }).sort(function (a, b) { return b.valor - a.valor; });

  // Asignación vs objetivo (sobre posiciones + efectivo)
  function alloc(key, label, eur, tgt) {
    var actual = posTotal ? eur / posTotal : 0;
    return { key: key, label: label, eur: eur, actual: actual, target: tgt,
      driftPp: tgt === null ? null : (actual - tgt) * 100, rebalanceEur: tgt === null ? null : (tgt - actual) * posTotal };
  }
  var allocation = [];
  if (hasPos) {
    allocation = [alloc('rv', 'Renta variable', rvEur, targets.rv), alloc('rf', 'Renta fija', rfEur, targets.rf), alloc('cash', 'Efectivo', cash, targets.cash)];
    if (otrosEur > 0.005) allocation.push(alloc('otros', 'Otros', otrosEur, null));
  }

  var geo = Object.keys(geoTotals).map(function (gk) {
    return { key: gk, label: GEO_LABELS[gk] || gk, eur: geoTotals[gk], pct: invPos ? geoTotals[gk] / invPos : 0 };
  }).filter(function (g) { return g.eur > 0.005; }).sort(function (a, b) { return (a.key === 'OTHER') - (b.key === 'OTHER') || b.eur - a.eur; });

  // Concentración
  var byInstr = {};
  positions.forEach(function (p) {
    var e = byInstr[p.id] || (byInstr[p.id] = { id: p.id, nombre: p.nombre, kind: p.kind, valor: 0, cuentas: 0 });
    e.valor += p.valor; e.cuentas++;
  });
  var instruments = Object.keys(byInstr).map(function (id) { var e = byInstr[id]; e.pesoInv = invPos ? e.valor / invPos : 0; return e; })
    .sort(function (a, b) { return b.valor - a.valor; });
  var top5 = positions.slice(0, 5).map(function (p, n) { return { rank: n + 1, nombre: p.nombre, cuenta: p.cuenta, id: p.id, kind: p.kind, valor: p.valor, pesoInv: p.pesoInv }; });
  var hhi = instruments.reduce(function (s, e) { return s + e.pesoInv * e.pesoInv; }, 0);
  var concentration = {
    nFondos: positions.filter(function (p) { return p.kind === 'fondo'; }).length,
    nAcciones: positions.filter(function (p) { return p.kind === 'stock'; }).length,
    nPosiciones: positions.length, nInstrumentos: instruments.length,
    top5: top5, top5Peso: top5.reduce(function (s, p) { return s + (p.pesoInv || 0); }, 0),
    mayorInstrumento: instruments[0] || null, hhi: hhi, nEfectivo: hhi ? 1 / hhi : null
  };

  // Drawdown sobre la rentabilidad acumulada (TWR): no lo distorsionan las aportaciones
  var chain = x.miChain, peak = -Infinity, peakDate = null, maxDd = 0, ddPeak = null, ddTrough = null;
  chain.forEach(function (c) {
    if (c.idx > peak) { peak = c.idx; peakDate = c.date; }
    var dd = peak > 0 ? c.idx / peak - 1 : 0;
    if (dd < maxDd) { maxDd = dd; ddPeak = peakDate; ddTrough = c.date; }
  });
  var drawdown = chain.length ? { max: maxDd, peakDate: ddPeak, troughDate: ddTrough, current: peak > 0 ? chain[chain.length - 1].idx / peak - 1 : null, lastPeakDate: peakDate } : null;

  // Fuente / frescura
  var lastBroker = null;
  H.rows.concat(P.rows).forEach(function (r) { if (r.source === 'broker' && (!lastBroker || r.date > lastBroker)) lastBroker = r.date; });
  var asOf = lastHist ? lastHist.date : lastPosDate;
  var lastSource = lastHist ? lastHist.source : latest.source;
  var navDates = latest.rows.filter(function (p) { return p.kind === 'fondo' && p.navDate; }).map(function (p) { return p.navDate; }).sort();

  var checkDiff = (hasPos && lastHist && lastHist.date === lastPosDate) ? posTotal - lastHist.total : null;
  var notes = [];
  if (hasPos && !lastHist) notes.push('Falta Historico: el patrimonio de MyInvestor es solo la suma de las posiciones (sin efectivo) y no hay evolución ni rentabilidad.');
  if (hasPos && lastHist && lastHist.date !== lastPosDate) notes.push('Las posiciones son del ' + lastPosDate + ' y el último Total de Historico del ' + lastHist.date + ': la asignación y el detalle se calculan con las posiciones.');
  if (checkDiff !== null && Math.abs(checkDiff) > 0.05) notes.push('La suma de posiciones + efectivo difiere del Total de Historico en ' + (Math.round(checkDiff * 100) / 100) + ' €.');

  var flows = x.miFlows, yr = x.asOf ? x.asOf.substring(0, 4) : '', mo = x.asOf ? x.asOf.substring(0, 7) : '';
  function sumIf(fn) { return flows.reduce(function (s, f) { return fn(f) ? s + f.importe : s; }, 0); }

  return {
    available: !!(lastHist || hasPos),
    reason: lastHist || hasPos ? '' : 'No hay datos de MyInvestor: faltan Historico y las pestañas de posiciones.',
    asOf: asOf, posDate: lastPosDate, hasPositions: hasPos, hasHistory: !!lastHist,
    source: {
      lastSource: lastSource, lastSourceLabel: sourceLabel_(lastSource), lastDate: asOf, lastBrokerDate: lastBroker,
      cashDate: cashOfficial || (cashPoint ? cashPoint.date : null), cashSource: cashOfficial ? 'broker' : (cashPoint ? cashPoint.source : null),
      navFrom: navDates[0] || null, navTo: navDates[navDates.length - 1] || null
    },
    totals: {
      patrimonio: patrimonio, inversiones: inversionesEur, fondos: fondosEur, acciones: accionesEur, efectivo: efectivoEur,
      cashPct: patrimonio && efectivoEur !== null ? efectivoEur / patrimonio : null,
      fondosPct: patrimonio && fondosEur !== null ? fondosEur / patrimonio : null,
      accionesPct: patrimonio && accionesEur !== null ? accionesEur / patrimonio : null,
      coste: costeKnown ? costeTot : null, plusvalia: costeKnown ? plusTot : null, plusvaliaPct: costeKnown && costeTot ? plusTot / costeTot : null,
      posTotal: hasPos ? posTotal : null, invPos: hasPos ? invPos : null, checkDiff: checkDiff
    },
    last: lastChange_(chain),
    weekPositionsFrom: weekRefDate,
    ter: { fondos: terBase ? terWeighted / terBase : null, inversiones: invPos ? annualCost / invPos : null, costeAnualEur: hasPos ? annualCost : null, sinDatoEur: terMissing },
    allocation: allocation, targets: targets,
    geo: geo, geoMissingEur: geoMissing,
    positions: positions, concentration: concentration,
    drawdown: drawdown,
    flows: {
      known: x.flowsKnown, flujosFound: x.flujosFound,
      items: flows.slice().reverse().slice(0, 100), n: flows.length,
      total: sumIf(function () { return true; }),
      aportaciones: sumIf(function (f) { return f.importe > 0; }),
      retiradas: sumIf(function (f) { return f.importe < 0; }),
      mes: sumIf(function (f) { return f.date.substring(0, 7) === mo; }),
      anio: sumIf(function (f) { return f.date.substring(0, 4) === yr; })
    },
    notes: notes
  };
}

/** Benchmark: sin salir de la hoja, se aproxima con el valor liquidativo del fondo indexado que lo replica. */
function buildBenchmark_(name, ref, P) {
  var want = hkey_(name), proxy = null;
  if (want) Object.keys(ref).forEach(function (isin) {
    var b = hkey_(ref[isin].benchmark);
    if (!b || b.indexOf(want) !== 0) return;
    if (!proxy || (ref[isin].ter !== null && (ref[proxy].ter === null || ref[isin].ter < ref[proxy].ter))) proxy = isin;
  });
  var nameFromPos = '';
  if (!proxy && want) P.rows.forEach(function (r) { if (!proxy && r.kind === 'fondo' && hkey_(r.nombre).indexOf(want) >= 0) { proxy = r.id; nameFromPos = r.nombre; } });
  var info = { name: name, available: false, proxyId: proxy, proxyName: proxy ? ((ref[proxy] && ref[proxy].nombre) || nameFromPos) : '', ter: proxy && ref[proxy] ? ref[proxy].ter : null, reason: '' };
  if (!proxy) { info.reason = 'No hay en la cartera ningún fondo indexado al ' + name + ' con el que aproximar el índice.'; return { info: info, points: [] }; }
  var by = {};
  P.rows.forEach(function (r) {
    if (r.id !== proxy) return;
    var px = r.nav !== null ? r.nav : (r.cantidad ? r.valor / r.cantidad : null);
    if (px === null || px <= 0) return;
    var cur = by[r.date];
    if (!cur || r.source === 'broker' || cur.source !== 'broker') by[r.date] = { date: r.date, value: px, source: r.source };
  });
  var points = Object.keys(by).sort().map(function (d) { return by[d]; });
  if (points.length < 2) { info.reason = 'Aún no hay suficientes valores liquidativos del fondo de referencia.'; return { info: info, points: points }; }
  info.available = true; info.from = points[0].date; info.to = points[points.length - 1].date;
  return { info: info, points: points };
}

/* ===================== Patrimonio total y rentabilidad ===================== */

/** Unión de fechas de Historico y Crypto; cada bloque arrastra su último valor conocido (forward-fill). t = total. */
function totalSeries_(history, crDays) {
  var dates = {}, hi = 0, ci = 0, h = null, c = null;
  history.forEach(function (x) { dates[x.date] = true; });
  crDays.forEach(function (x) { dates[x.date] = true; });
  return Object.keys(dates).sort().map(function (d) {
    while (hi < history.length && history[hi].date <= d) h = history[hi++];
    while (ci < crDays.length && crDays[ci].date <= d) c = crDays[ci++];
    return { d: d, mi: h ? h.total : null, cr: c ? c.valor : null, t: (h ? h.total : 0) + (c ? c.valor : 0), miFill: !!h && h.date !== d, crFill: !!c && c.date !== d,
      src: h ? h.source : null, f: h ? h.fondos : null, a: h ? h.acciones : null, e: h ? h.efectivo : null };
  });
}

/**
 * Rentabilidad ponderada por tiempo (TWR): se encadenan los subperiodos entre valoraciones consecutivas.
 * En cada subperiodo se aplica Dietz modificado:  r = (Vfin − Vini − F) / (Vini + Σ w·f)
 * donde F son los flujos del subperiodo y w la fracción del subperiodo que cada flujo estuvo dentro.
 * Así las aportaciones y retiradas no cuentan como rentabilidad.
 *   points: [{date, value}] ordenados, una valoración por fecha.
 *   flows:  [{date, amount, eff}] amount > 0 = entra dinero. eff (opcional) = fecha de la valoración en la que
 *           el flujo ya está dentro del valor (por defecto, la primera valoración con fecha >= date).
 */
function twrChain_(points, flows) {
  var chain = points.map(function (p) { return { date: p.date, value: p.value, flow: 0, wflow: 0, gain: 0, r: null, idx: 1, items: [] }; });
  if (!chain.length) return chain;
  (flows || []).forEach(function (f) {
    if (!f.amount) return;
    var i = firstIndexGE_(chain, f.eff || f.date);
    if (i <= 0) return;   // anterior a la primera valoración (ya incluido en ella) o posterior a la última (aún no reflejado)
    var a = chain[i - 1].date, b = chain[i].date, span = daysBetween_(a, b);
    var t = f.date < a ? a : (f.date > b ? b : f.date);
    var w = span > 0 ? daysBetween_(t, b) / span : 0;
    chain[i].flow += f.amount; chain[i].wflow += f.amount * w; chain[i].items.push({ date: t, amount: f.amount });
  });
  for (var n = 1; n < chain.length; n++) {
    var c = chain[n], prev = chain[n - 1], base = prev.value + c.wflow;
    c.gain = c.value - prev.value - c.flow;
    c.r = base > 1e-9 ? c.gain / base : null;
    c.idx = prev.idx * (1 + (c.r === null ? 0 : c.r));
  }
  return chain;
}

/**
 * Resultado de un periodo [startKey, endKey] sobre una cadena TWR. El punto de partida es la última valoración
 * con fecha <= startKey; si el historial empieza después, se usa el primer registro y partial = true.
 * Devuelve TWR, Dietz modificado del periodo completo (ponderado por dinero), ganancia en € y flujos.
 */
function periodStats_(chain, startKey, endKey) {
  if (!chain.length) return null;
  var b = lastIndexLE_(chain, endKey); if (b < 0) return null;
  var a = startKey === null ? 0 : lastIndexLE_(chain, startKey), partial = false;
  if (a < 0) { a = 0; partial = true; }
  var D = daysBetween_(chain[a].date, chain[b].date);
  var out = { from: chain[a].date, to: chain[b].date, days: D, partial: partial, startValue: chain[a].value, endValue: chain[b].value,
    twr: null, dietz: null, gain: null, flows: null, annualized: null };
  if (b <= a) return out;
  var gain = 0, fl = 0, wsum = 0;
  for (var i = a + 1; i <= b; i++) {
    gain += chain[i].gain; fl += chain[i].flow;
    chain[i].items.forEach(function (it) { wsum += it.amount * (D > 0 ? daysBetween_(it.date, chain[b].date) / D : 0); });
  }
  var denom = chain[a].value + wsum;
  out.gain = gain; out.flows = fl;
  out.twr = chain[a].idx > 0 ? chain[b].idx / chain[a].idx - 1 : null;
  out.dietz = denom > 1e-9 ? gain / denom : null;
  if (out.twr !== null && D >= 365 && out.twr > -1) out.annualized = Math.pow(1 + out.twr, 365 / D) - 1;
  return out;
}

function periodDefs_(asOf) {
  return [
    { key: '1d', label: '1 día', start: shiftDays_(asOf, -1) },
    { key: '7d', label: '7 días', start: shiftDays_(asOf, -7) },
    { key: '1m', label: '1 mes', start: shiftMonths_(asOf, -1) },
    { key: '3m', label: '3 meses', start: shiftMonths_(asOf, -3) },
    { key: 'ytd', label: 'Año (YTD)', start: yearStart_(asOf) },
    { key: '1a', label: '1 año', start: shiftMonths_(asOf, -12) },
    { key: 'inicio', label: 'Desde inicio', start: null }
  ];
}
/** 31 de diciembre del año anterior (punto de partida del YTD). */
function yearStart_(asOf) { return (parseInt(asOf.substring(0, 4), 10) - 1) + '-12-31'; }

/** Inicio de cada periodo de los gráficos (selector «1 mes · 3 meses · YTD · 1 año · Todo»). */
function rangeStarts_(asOf) {
  if (!asOf) return {};
  return { '1m': shiftMonths_(asOf, -1), '3m': shiftMonths_(asOf, -3), 'ytd': yearStart_(asOf), '1a': shiftMonths_(asOf, -12), 'todo': null };
}

/** Variación del último dato frente al anterior (ya sin flujos). */
function lastChange_(chain) {
  if (chain.length < 2) return null;
  var c = chain[chain.length - 1], p = chain[chain.length - 2];
  return { from: p.date, to: c.date, eur: c.gain, pct: c.r, flows: c.flow };
}

/** Índices acumulados (1 = primer registro de cada serie) sobre la unión de fechas, arrastrando el último valor. */
function indexSeries_(chains) {
  var keys = Object.keys(chains), dates = {}, pos = {}, cur = {};
  keys.forEach(function (k) { chains[k].forEach(function (c) { dates[c.date] = true; }); pos[k] = 0; cur[k] = null; });
  return Object.keys(dates).sort().map(function (d) {
    var row = { d: d };
    keys.forEach(function (k) {
      var ch = chains[k];
      while (pos[k] < ch.length && ch[pos[k]].date <= d) cur[k] = ch[pos[k]++].idx;
      row[k] = cur[k];
    });
    return row;
  });
}

/**
 * MyInvestor frente al índice para cada periodo de los gráficos: filas desde la última fecha ≤ inicio del periodo
 * (o desde el primer dato común), en % acumulado desde ese punto, y la diferencia en puntos.
 */
function benchRanges_(index, starts, withBench) {
  var out = {};
  Object.keys(starts).forEach(function (k) {
    var s = starts[k], rows = index, i0 = -1, st = -1;
    if (s) { index.forEach(function (r, n) { if (r.d <= s) i0 = n; }); if (i0 >= 0) rows = index.slice(i0); }
    rows.forEach(function (r, i) { if (st < 0 && isNum_(r.mi) && (!withBench || isNum_(r.bench))) st = i; });
    rows = st < 0 ? [] : rows.slice(st);
    // el arrastre de fechas de otros bloques no aporta puntos nuevos a esta comparación
    rows = rows.filter(function (r, i) { return i === 0 || r.mi !== rows[i - 1].mi || r.bench !== rows[i - 1].bench; });
    var b0 = rows[0];
    out[k] = rows.map(function (r) {
      var mi = isNum_(r.mi) && b0.mi ? (r.mi / b0.mi - 1) * 100 : null;
      var be = withBench && isNum_(r.bench) && b0.bench ? (r.bench / b0.bench - 1) * 100 : null;
      return { d: r.d, mi: mi, bench: be, diff: mi !== null && be !== null ? mi - be : null };
    });
  });
  return out;
}

/* ===================== v4 · Rendimiento diario, drivers y base 100 ===================== */

/**
 * Vista diaria: filas día a día (MyInvestor, BTC y total con BTC, solo efecto mercado) y, para cada ventana
 * (1 día, 20 días, 3 meses, YTD), las contribuciones por activo enlazadas con Carino, la contribución
 * acumulada día a día y la base 100 por activo. Todo con números: Index.html solo lo pinta.
 */
function buildDaily_(x) {
  var PP = positionPrices_(x.P);
  var assets = dailyAssets_(PP, x.ref);
  var names = {}; assets.forEach(function (a) { names[a.id] = a.short; });
  var useHist = x.history.length >= 2;
  var mi = miDaily_(PP, x.history, x.miChain, names);
  var bt = btcDaily_(x.crDays, x.crChain, x.mainAsset);
  var rows = unionDaily_(mi, bt, x.crDays, x.totalChain, useHist);
  var detail = mi.rows.filter(function (r) { return r.detail; });
  var out = {
    available: rows.length > 0, reason: '', asOf: x.asOf, first: rows.length ? rows[0].prev : null,
    detailFrom: detail.length ? detail[0].prev : null, detailTo: detail.length ? detail[detail.length - 1].d : null,
    posFrom: PP.dates[0] || null, hasPositions: PP.keys.length > 0, hasHistory: useHist, btcAsset: x.mainAsset,
    btcFrom: x.crDays.length ? x.crDays[0].date : null,
    assets: assets, rows: rows, windows: {}, days: {}, picks: []
  };
  if (!rows.length) {
    out.reason = !x.history.length && !PP.keys.length && x.crDays.length < 2 ? 'Hacen falta al menos dos días de Historico, posiciones o cripto.'
      : 'Hacen falta al menos dos días de datos para ver el rendimiento diario.';
    return out;
  }
  var firstSeen = {}; assets.forEach(function (a) { firstSeen[a.id] = a.first; });
  var ctx = { ids: assets.map(function (a) { return a.id; }), firstSeen: firstSeen, btcFrom: out.btcFrom };
  DAILY_WINDOWS.forEach(function (def) {
    var w = windowRows_(rows, def, x.asOf);
    var st = windowStats_(w.rows, ctx, def.key !== '1d');
    st.key = def.key; st.label = def.label; st.partial = w.partial; st.start = w.start || null; st.i0 = rows.length - w.rows.length;
    out.windows[def.key] = st;
  });
  // «1 día» se puede mirar día a día en las últimas sesiones (las del gráfico de 20 días)
  rows.slice(-DAY_PICKS).forEach(function (r, i, arr) {
    var st = windowStats_([r], ctx, false);
    st.key = '1d'; st.label = '1 día'; st.partial = false; st.start = null; st.i0 = rows.length - arr.length + i;
    out.days[r.d] = st; out.picks.push(r.d);
  });
  return out;
}

/**
 * Precio de cada posición (ISIN + cuenta) en cada fecha con fotos de posiciones, listo para atribuir el movimiento:
 *  · Unidades: Cantidad; si falta, la última conocida (o la siguiente, al principio).
 *  · Precio en EUR: fondos -> NAV (valor liquidativo); si falta, Valor_EUR / unidades.
 *    Acciones -> Valor_EUR / unidades, que ya incluye el efecto divisa (o el precio de la hoja si cuadra con eso).
 *    El precio en USD de la nota («Yahoo 281,35 USD») solo sirve para separar la parte del dólar.
 *  · Fondos y nav_date: el movimiento cae el día en que cambia el VL; un VL con nav_date anterior al ya usado
 *    se ignora (no hay saltos atrás). Un día sin fila de una posición que sigue en cartera repite su último precio.
 * Estado por fecha: { u, px, navDate, mv: inicio|nuevo|igual|corregido|antiguo|sinfila, est, udsEst, usd, fx }
 */
function positionPrices_(P) {
  var dates = P.dates, snaps = dates.map(function (d) { return snapshot_(P, d); }), byKey = {}, keys = [];
  snaps.forEach(function (s, i) {
    s.rows.forEach(function (r) {
      var k = r.id + '|' + r.cuenta, K = byKey[k];
      if (!K) { K = byKey[k] = { key: k, id: r.id, cuenta: r.cuenta, kind: r.kind, first: i, last: i, rows: {}, st: {}, mercado: '' }; keys.push(K); }
      K.last = i; K.rows[i] = r; K.nombre = r.nombre; if (r.mercado) K.mercado = r.mercado;
    });
  });
  // Orden fijo (ISIN + cuenta): el resultado no depende de en qué pestaña u orden vengan las filas
  keys.sort(function (a, b) { return a.key < b.key ? -1 : a.key > b.key ? 1 : 0; });
  keys.forEach(function (K) {
    var i, u = null, units = {}, firstU = null, prev = null;
    for (i = K.first; i <= K.last; i++) { var r0 = K.rows[i]; if (r0 && r0.cantidad !== null && r0.cantidad > 0) u = r0.cantidad; units[i] = u; if (firstU === null && u !== null) firstU = u; }
    for (i = K.first; i <= K.last && units[i] === null; i++) units[i] = firstU;
    K.ccy = K.kind === 'stock' ? stockCcy_(K.id, K.mercado) : 'EUR';
    for (i = K.first; i <= K.last; i++) {
      var row = K.rows[i], s = null;
      if (!row) {
        if (prev) s = { u: prev.u, px: prev.px, navDate: prev.navDate, mv: 'sinfila', est: prev.est, udsEst: false, usd: prev.usd, fx: prev.fx };
      } else {
        var px = priceOf_(row, units[i], K.kind), q = parseQuote_(row.nota);
        s = { u: units[i], px: px, navDate: row.navDate, mv: '', est: K.kind === 'fondo' && isNavEst_(row.nota), udsEst: isUdsEst_(row.nota),
          usd: K.ccy === 'USD' ? (q.usd !== null ? q.usd : localPrice_(row, px)) : null, fx: q.fx };
        if (s.px === null && prev) s.px = prev.px;
        if (K.kind === 'fondo') {
          if (!prev) s.mv = 'inicio';
          else if (s.navDate && prev.navDate && s.navDate < prev.navDate) { s.px = prev.px; s.navDate = prev.navDate; s.est = prev.est; s.mv = 'antiguo'; }
          else if (s.navDate && s.navDate !== prev.navDate) s.mv = 'nuevo';
          else if (s.px !== null && prev.px !== null && Math.abs(s.px - prev.px) > 1e-9) s.mv = s.navDate ? 'corregido' : 'nuevo';
          else s.mv = 'igual';
        }
      }
      K.st[i] = s; if (s) prev = s;
    }
  });
  return { dates: dates, snaps: snaps, keys: keys };
}

/** Precio en EUR para atribuir: fondos -> NAV; acciones -> Valor_EUR / unidades, o el precio de la hoja si cuadra con eso. */
function priceOf_(r, u, kind) {
  var byVal = u ? r.valor / u : null;
  if (r.nav !== null && r.nav > 0 && (kind === 'fondo' || byVal === null || Math.abs(r.nav - byVal) <= 0.005 * Math.abs(byVal))) return r.nav;
  return byVal;
}
/** Precio en divisa local cuando la hoja trae uno que no es el de euros (p. ej. Precio en USD). */
function localPrice_(r, px) { return r.nav !== null && r.nav > 0 && px !== null && Math.abs(r.nav - px) > 0.005 * Math.abs(px) ? r.nav : null; }
function stockCcy_(id, mercado) { return STOCK_CCY[String(id).toUpperCase()] || MARKET_CCY[String(mercado || '').toUpperCase()] || null; }

/** De la nota de una acción: precio en USD («Yahoo 281,35 USD») y EURUSD («EURUSD 1,1171»), si vienen. */
function parseQuote_(nota) {
  var s = String(nota || ''), m, out = { usd: null, fx: null };
  if ((m = s.match(/(\d[\d.,]*\d|\d)\s*USD\b/))) out.usd = num_(m[1]);
  if ((m = s.match(/EUR\s*\/?\s*USD\D{0,14}?(\d+[.,]\d+)/i))) { var fx = parseFloat(m[1].replace(',', '.')); out.fx = isFinite(fx) && fx > 0.3 && fx < 3 ? fx : null; }
  return out;
}
/** ¿La nota marca el valor liquidativo como estimado? («NAV est 1,3209», «est 1.29335», «from prior»). */
function isNavEst_(nota) {
  var s = String(nota || '').replace(/corrig\w*\s+(?:la\s+)?est\w*\.?\s*[\d.,]*/ig, '');
  return /\b(?:nav|vl)\s+est(?:imad[oa])?\b/i.test(s) || /\best(?:imad[oa])?\.?\s+\d/i.test(s) || /\bfrom prior\b/i.test(s);
}
/** ¿La nota marca las unidades como estimadas? («+6,8166 uds aportación jueves 100€ est.») */
function isUdsEst_(nota) { return /\buds?\b[^;]*\best\b/i.test(String(nota || '')); }

/** Activos de la vista diaria (ISIN o ticker, sumando cuentas), ordenados por valor en la última foto. */
function dailyAssets_(PP, ref) {
  var by = {}, list = [], lastIdx = PP.dates.length - 1;
  PP.keys.forEach(function (K) {
    var a = by[K.id];
    if (!a) { a = by[K.id] = { id: K.id, kind: K.kind, ccy: K.ccy, name: (ref[K.id] && ref[K.id].nombre) || K.nombre, first: PP.dates[K.first], last: PP.dates[K.last], v: 0, cuentas: 0 }; list.push(a); }
    a.cuentas++;
    if (PP.dates[K.first] < a.first) a.first = PP.dates[K.first];
    if (PP.dates[K.last] > a.last) a.last = PP.dates[K.last];
    var s = K.last === lastIdx ? K.st[lastIdx] : null;
    if (s && s.u && s.px !== null) a.v += s.u * s.px;
  });
  list.forEach(function (a) { a.short = shortName_(a.id, a.name); });
  return list.sort(function (a, b) { return b.v - a.v || (a.id < b.id ? -1 : 1); });
}
function shortName_(id, name) {
  var k = String(id).toUpperCase();
  if (SHORT_NAMES[k]) return SHORT_NAMES[k];
  var s = String(name || id).replace(/\b(fund|fondo|index|investor|class|clase|acc|eur|usd|hedged)\b.*$/i, '').trim();
  return (s.split(/\s+/).slice(0, 3).join(' ') || String(id)).substring(0, 24);
}

/**
 * MyInvestor día a día, SOLO efecto mercado, entre cada dos fechas consecutivas de Historico (o de posiciones si no
 * hay Historico). Por activo (ISIN o ticker, sumando sus cuentas):
 *   €            = Σ unidades del día anterior × (precio de hoy − precio del día anterior)
 *   contribución = € / patrimonio del día anterior   (= peso del día anterior × rendimiento del día)
 * Las aportaciones internas de los jueves (caja -> fondo) cambian unidades y caja, no el resultado: el día de la compra
 * las unidades nuevas no tienen «día anterior». La caja es un activo más que rinde 0 %.
 * Residuo del día = TWR de Historico (v3, sin flujos) − Σ contribuciones; debe ser ≈ 0.
 */
function miDaily_(PP, history, miChain, names) {
  var useHist = history.length >= 2, rows = [];
  var dates = useHist ? history.map(function (h) { return h.date; }) : PP.dates.slice();
  var pIdx = {}, hBy = {}, cBy = {};
  PP.dates.forEach(function (d, i) { pIdx[d] = i; });
  history.forEach(function (h) { hBy[h.date] = h; });
  miChain.forEach(function (c) { cBy[c.date] = c; });
  var vals = dates.map(function (d) { return { d: d, v: useHist ? hBy[d].total : posValue_(PP, pIdx[d]) }; });
  // Descuadre de la hoja en cada fecha: Total de Historico − (Σ Valor_EUR de las posiciones + efectivo).
  // Si una foto no cuadra, sus posiciones no son fiables: los días que la tocan van a «sin detalle» (no se reparten).
  var desc = {}, bad = {};
  if (useHist) dates.forEach(function (d) {
    var h = hBy[d], i = pIdx[d];
    if (i === undefined || h.efectivo === null) return;
    desc[d] = h.total - PP.snaps[i].rows.reduce(function (s, r) { return s + r.valor; }, 0) - h.efectivo;
    if (Math.abs(desc[d]) > Math.max(2, 1e-4 * Math.abs(h.total))) bad[d] = true;
  });
  for (var n = 1; n < dates.length; n++) {
    var a = dates[n - 1], b = dates[n], ia = a in pIdx ? pIdx[a] : -1, ib = b in pIdx ? pIdx[b] : -1;
    var detail = ia >= 0 && ib >= 0 && !bad[a] && !bad[b], base = vals[n - 1].v, A = {}, sumE = 0, sumV = 0, udsEst = false, fx = null;
    var nodet = detail ? null : ia < 0 || ib < 0 ? { motivo: 'sinpos', d: ia < 0 ? a : b } : { motivo: 'descuadre', d: bad[a] ? a : b, eur: bad[a] ? desc[a] : desc[b] };
    if (detail) PP.keys.forEach(function (K) {
      if (K.first > ia || K.last < ib) return;   // compra o venta dentro del tramo: es un flujo caja <-> activo, no mercado
      var e = 0, j;
      for (j = ia + 1; j <= ib; j++) {
        var s0 = K.st[j - 1], s1 = K.st[j];
        if (s0 && s1 && s0.u && s0.px !== null && s1.px !== null) e += s0.u * (s1.px - s0.px);
        if (s1 && s1.udsEst) udsEst = true;
        if (s1 && s1.fx && fx === null && j === ib) fx = s1.fx;
      }
      var sa = K.st[ia], sb = K.st[ib], v = sa && sa.u && sa.px !== null ? sa.u * sa.px : 0;
      var x = A[K.id] || (A[K.id] = { e: 0, v: 0 });
      x.e += e; x.v += v; sumE += e; sumV += v;
      if (K.kind === 'fondo') {
        if (x.nav === undefined) { x.nav = sb.navDate || null; x.mv = moveOf_(K, ia, ib); }
        if (sb.est) x.est = true;
      } else if (K.ccy === 'USD' && sa.usd && sb.usd && x.rl === undefined) x.rl = sb.usd / sa.usd - 1;
    });
    var out = {}, est = [];
    Object.keys(A).forEach(function (id) {
      var x = A[id], r = x.v > 0 ? x.e / x.v : null, o = { e: x.e, c: base > 0 ? x.e / base : null, r: r, w: base > 0 ? x.v / base : null };
      if (x.nav !== undefined) { o.nav = x.nav; o.mv = x.mv; if (x.est) { o.est = true; est.push(names[id] || id); } }
      if (x.rl !== undefined && r !== null) { o.rl = x.rl; o.rfx = (1 + r) / (1 + x.rl) - 1; }
      out[id] = o;
    });
    var c = cBy[b], hR = useHist ? (c && c.r !== null ? c.r : 0) : (base > 0 ? sumE / base : null);
    var gainH = useHist ? (c ? c.gain : 0) : sumE, pct = detail && base > 0 ? sumE / base : null;
    var src = detail ? PP.snaps[ib].source : (hBy[b] ? hBy[b].source : null);
    if (fx === null && hBy[b]) fx = parseQuote_(hBy[b].nota).fx;
    var why = [];
    if (src !== 'broker') why.push(src === 'public_markets' ? 'precios de mercado (public_markets)' : 'fuente ' + (src || 'n/d'));
    if (est.length) why.push('VL estimado: ' + est.join(', '));
    if (udsEst) why.push('unidades estimadas (aportación del jueves)');
    if (!detail) why.push('sin detalle por posición: solo el total de Historico');
    var dsc = desc[a] !== undefined && desc[b] !== undefined && (Math.abs(desc[a]) > 0.5 || Math.abs(desc[b]) > 0.5) ? { prev: desc[a], cur: desc[b] } : null;
    rows.push({ d: b, prev: a, detail: detail, nodet: nodet, base: base, e: detail ? sumE : null, pct: pct, h: hR, gainH: gainH,
      res: detail && hR !== null && pct !== null ? hR - pct : null, resE: detail ? gainH - sumE : null,
      cashW: detail && base > 0 ? (base - sumV) / base : null, a: out, desc: dsc,
      src: src === 'broker' && !est.length ? 'broker' : 'estimado', fuente: src, why: why, fx: fx, gap: workdayGap_(a, b) });
  }
  return { rows: rows, vals: vals };
}

/** Cómo cambió el VL de un fondo entre dos fotos: nuevo > corregido > antiguo (ignorado) > sin fila > igual. */
function moveOf_(K, ia, ib) {
  var rank = { nuevo: 5, corregido: 4, antiguo: 3, sinfila: 2, igual: 1, inicio: 0, '': 0 }, best = 'igual';
  for (var j = ia + 1; j <= ib; j++) { var s = K.st[j]; if (s && rank[s.mv] > rank[best]) best = s.mv; }
  return best;
}
/** Valor de las posiciones de una foto (sin efectivo): solo se usa si falta Historico. */
function posValue_(PP, i) {
  var v = 0;
  PP.keys.forEach(function (K) { var s = i !== undefined ? K.st[i] : null; if (s && s.u && s.px !== null) v += s.u * s.px; });
  return v;
}

/**
 * Cripto día a día: € = cantidad del día anterior × (precio de hoy − precio anterior), por activo y plataforma.
 * Si entre dos filas hay días naturales sin dato (la hoja no tiene sábados ni domingos), la barra de la fila siguiente
 * acumula ese movimiento y se marca («incluye fin de semana»): nunca se inventan barras para los días sin fila.
 */
function btcDaily_(crDays, crChain, main) {
  var rows = [], cBy = {};
  crChain.forEach(function (c) { cBy[c.date] = c; });
  for (var n = 1; n < crDays.length; n++) {
    var a = crDays[n - 1], b = crDays[n], prev = {}, e = 0;
    a.items.forEach(function (it) { prev[it.activo + '|' + it.plataforma.toLowerCase()] = it; });
    b.items.forEach(function (it) {
      var p = prev[it.activo + '|' + it.plataforma.toLowerCase()]; if (!p) return;   // activo nuevo: es un flujo, no mercado
      e += p.cantidad !== null && p.precio !== null && it.precio !== null ? p.cantidad * (it.precio - p.precio) : it.valor - p.valor;
    });
    var c = cBy[b.date], pa = assetAgg_(a, main).precio, pb = assetAgg_(b, main).precio;
    rows.push({ d: b.date, prev: a.date, e: e, base: a.valor, r: a.valor > 0 ? e / a.valor : null,
      h: c && c.r !== null ? c.r : null, gainH: c ? c.gain : null, px0: pa, px: pb, src: b.source, gap: gapInfo_(a.date, b.date) });
  }
  return rows;
}

/** Días naturales sin fila entre dos fechas consecutivas (sin contar ninguna de las dos), con su etiqueta. */
function gapInfo_(a, b) {
  var n = daysBetween_(a, b) - 1; if (n <= 0) return null;
  var we = 0, lastWe = null;
  for (var i = 1; i <= n; i++) { var dow = new Date(keyMs_(a) + i * 86400000).getUTCDay(); if (dow === 0 || dow === 6) { we++; lastWe = dow; } }
  var parts = [], wd = n - we;
  if (we) parts.push(we === 1 ? (lastWe === 6 ? 'el sábado' : 'el domingo') : we === 2 ? 'fin de semana' : Math.ceil(we / 2) + ' fines de semana');
  if (wd) parts.push(wd + (wd === 1 ? ' día laborable' : ' días laborables') + ' sin dato');
  return { n: n, finde: we, laborables: wd, from: shiftDays_(a, 1), to: shiftDays_(b, -1), label: 'incluye ' + parts.join(' y ') };
}
/** Para MyInvestor solo cuentan los días laborables sin dato (en fin de semana los mercados no abren). */
function workdayGap_(a, b) {
  var g = gapInfo_(a, b); if (!g || !g.laborables) return null;
  return { n: g.laborables, from: g.from, to: g.to, label: 'incluye ' + g.laborables + (g.laborables === 1 ? ' día laborable' : ' días laborables') + ' sin dato' };
}

/**
 * Filas de la vista «total con BTC»: unión de las fechas de MyInvestor y de la cripto. Cada bloque aporta su movimiento
 * el día que tiene fila (un día sin fila aporta 0 y su movimiento cae en su fila siguiente).
 *   total € = MyInvestor € + BTC €;  total % = total € / (MyInvestor + cripto del día anterior, arrastrando el último valor)
 * h = TWR del día del total según v3 (cadena con flujos); cMi y cBtc = aportación de cada bloque al total.
 */
function unionDaily_(mi, bt, crDays, totalChain, useHist) {
  var dates = {}, miBy = {}, btBy = {}, tBy = {}, out = [];
  mi.rows.forEach(function (r) { dates[r.d] = dates[r.prev] = true; miBy[r.d] = r; });
  bt.forEach(function (r) { dates[r.d] = dates[r.prev] = true; btBy[r.d] = r; });
  totalChain.forEach(function (c) { tBy[c.date] = c; });
  var im = 0, ic = 0, mv = null, cv = null, prevKey = null, prevBase = null;
  Object.keys(dates).sort().forEach(function (d) {
    while (im < mi.vals.length && mi.vals[im].d <= d) mv = mi.vals[im++].v;
    while (ic < crDays.length && crDays[ic].date <= d) cv = crDays[ic++].valor;
    if (prevKey !== null) {
      var m = miBy[d] || null, b = btBy[d] || null, base = prevBase;
      var eMi = m ? (m.detail ? m.e : m.gainH) : 0, gMi = m ? m.gainH : 0, eB = b ? b.e : 0, t = tBy[d];
      var pct = base > 0 ? (eMi + eB) / base : null;
      var h = useHist ? (t && t.r !== null ? t.r : 0) : pct;
      var cMi = base > 0 ? gMi / base : null, cB = base > 0 ? eB / base : null;
      out.push({ d: d, prev: prevKey, days: daysBetween_(prevKey, d), mi: m, btc: b,
        src: (m ? m.src === 'broker' : true) && (b ? b.src === 'broker' : true) ? 'broker' : 'estimado',
        tot: { e: eMi + eB, base: base, pct: pct, h: h, gainH: useHist ? (t ? t.gain : 0) : eMi + eB, cMi: cMi, cBtc: cB,
          res: h !== null && cMi !== null && cB !== null ? h - cMi - cB : null } });
    }
    prevKey = d; prevBase = (mv || 0) + (cv || 0);
  });
  return out;
}

/** Filas de una ventana: «1 día» = la última; «20 días» = las 20 últimas; 3 meses / YTD desde la última fecha ≤ inicio. */
function windowRows_(rows, def, asOf) {
  if (def.key === '1d') return { rows: rows.slice(-1), partial: false };
  if (def.n) return { rows: rows.slice(-def.n), partial: rows.length < def.n };
  var start = def.key === 'ytd' ? yearStart_(asOf) : shiftMonths_(asOf, -def.months), i0 = -1;
  if (rows[0].prev > start) return { rows: rows, partial: true, start: start };
  rows.forEach(function (r, i) { if (r.prev <= start) i0 = i; });
  return { rows: rows.slice(i0), partial: false, start: start };
}

/** Enlace de Carino: k(r) = ln(1 + r) / r (y 1 en el límite r -> 0). */
function carinoK_(r) { return Math.abs(r) < 1e-12 ? 1 : Math.log(1 + r) / r; }

/**
 * Contribuciones de MyInvestor enlazadas con Carino sobre unas filas diarias.
 * Con r_t la TWR del día (Historico) y R = Π(1 + r_t) − 1 la del periodo:
 *   k_t = ln(1 + r_t) / r_t,   k = ln(1 + R) / R,   C_i = Σ_t c_i,t · k_t / k
 * Cada día Σ_i c_i,t + residuo_t = r_t, así que Σ_i C_i + residuo enlazado (+ días sin detalle) = R exactamente.
 * Los euros no necesitan enlace: se suman.
 */
function linkMi_(miRows) {
  var R = 1; miRows.forEach(function (m) { R *= 1 + (m.h || 0); }); R -= 1;
  var k = carinoK_(R), L = { twr: R, gain: 0, c: {}, e: {}, g: {}, wS: {}, wN: {}, res: 0, resE: 0, nod: 0, nodE: 0, nNod: 0, nodDays: [], nDet: 0, cashW: 0, posR: 1 };
  miRows.forEach(function (m) {
    var f = carinoK_(m.h || 0) / k;
    L.gain += m.gainH || 0;
    if (!m.detail) { L.nod += (m.h || 0) * f; L.nodE += m.gainH || 0; L.nNod++; L.nodDays.push({ d: m.d, eur: m.gainH, pct: m.h, nodet: m.nodet }); return; }
    L.nDet++; L.posR *= 1 + (m.pct || 0);
    Object.keys(m.a).forEach(function (id) {
      var x = m.a[id];
      L.c[id] = (L.c[id] || 0) + (x.c || 0) * f;
      L.e[id] = (L.e[id] || 0) + x.e;
      L.g[id] = (L.g[id] === undefined ? 1 : L.g[id]) * (1 + (x.r || 0));
      L.wS[id] = (L.wS[id] || 0) + (x.w || 0); L.wN[id] = (L.wN[id] || 0) + 1;
    });
    L.res += (m.res || 0) * f; L.resE += m.resE || 0; L.cashW += m.cashW || 0;
  });
  return L;
}

/**
 * Resultado de una ventana: drivers de MyInvestor (contribución enlazada en puntos y en €, rendimiento y peso medio
 * de cada activo, caja a 0 %, residuo y días sin detalle), BTC aparte, total con BTC (también enlazado),
 * base 100 y, si withCum, la contribución acumulada día a día.
 */
function windowStats_(rows, ctx, withCum) {
  var miRows = [], btRows = [];
  rows.forEach(function (r) { if (r.mi) miRows.push(r.mi); if (r.btc) btRows.push(r.btc); });
  var L = linkMi_(miRows);
  var items = Object.keys(L.c).map(function (id) {
    return { id: id, pp: L.c[id], eur: L.e[id], r: L.g[id] - 1, w: L.wN[id] ? L.wS[id] / L.wN[id] : null };
  }).sort(function (a, b) { return b.pp - a.pp; });
  var sum = items.reduce(function (s, it) { return s + it.pp; }, 0) + L.res + L.nod;
  var gB = 1, eB = 0, nGap = 0;
  btRows.forEach(function (b) { gB *= 1 + (b.r || 0); eB += b.e; if (b.gap) nGap++; });
  var T = 1; rows.forEach(function (r) { T *= 1 + (r.tot.h || 0); }); T -= 1;
  var kT = carinoK_(T), tMi = 0, tB = 0, tR = 0, gT = 0;
  rows.forEach(function (r) {
    var f = carinoK_(r.tot.h || 0) / kT;
    tMi += (r.tot.cMi || 0) * f; tB += (r.tot.cBtc || 0) * f; tR += (r.tot.res || 0) * f; gT += r.tot.gainH || 0;
  });
  var top = items.slice().sort(function (a, b) { return Math.abs(b.pp) - Math.abs(a.pp); })[0];
  // Días con residuo apreciable (≥ 1 €) y, si lo hay, el descuadre de la hoja que lo explica
  var resDays = miRows.filter(function (m) { return m.detail && Math.abs(m.resE || 0) >= 1; }).map(function (m) { return { d: m.d, eur: m.resE, pp: m.res, desc: m.desc }; });
  // Barras de los drivers, de más positiva a más negativa: activos, caja (rinde 0 %), días sin detalle y residuo si se nota
  var bars = items.map(function (it) { return { id: it.id, k: 'a', pp: it.pp, eur: it.eur, r: it.r, w: it.w }; });
  if (L.nDet) bars.push({ id: '_cash', k: 'cash', pp: 0, eur: 0, r: 0, w: L.cashW / L.nDet });
  if (L.nNod) bars.push({ id: '_nod', k: 'nod', pp: L.nod, eur: L.nodE, n: L.nNod });
  if (Math.abs(L.res) >= 5e-5 || Math.abs(L.resE) >= 5) bars.push({ id: '_res', k: 'res', pp: L.res, eur: L.resE });
  bars.sort(function (a, b) { return b.pp - a.pp || (a.k === 'cash') - (b.k === 'cash'); });
  return {
    from: rows[0].prev, to: rows[rows.length - 1].d, n: rows.length, nMi: miRows.length,
    mi: miRows.length ? {
      twr: L.twr, gain: L.gain, twrPos: L.nDet ? L.posR - 1 : null, items: items, bars: bars,
      cash: { w: L.nDet ? L.cashW / L.nDet : null, pp: 0, eur: 0 },
      res: { pp: L.res, eur: L.resE, days: resDays }, nod: { pp: L.nod, eur: L.nodE, n: L.nNod, days: L.nodDays }, nDet: L.nDet,
      sum: sum, check: sum - L.twr
    } : null,
    btc: btRows.length ? { r: gB - 1, eur: eB, pp: tB, n: btRows.length, nGap: nGap } : null,
    tot: { twr: T, gain: gT, mi: tMi, btc: tB, res: tR },
    sel: top ? top.id : null,
    idx: base100_(rows, ctx),
    cum: withCum && rows.length > 1 ? cumContrib_(rows, ctx.ids) : null
  };
}

/**
 * Base 100 al inicio de la ventana: cada activo con el rendimiento diario de su precio (atribuido como en la vista
 * diaria), MyInvestor con su TWR y BTC con el suyo. Los activos arrancan en 100 en el primer día con detalle por
 * posición de la ventana (o el día de su primer dato, si entran después); la cripto, en su primera valoración.
 * Un día sin detalle por posición deja planas las líneas de los activos. Enseña rendimiento, no peso.
 */
function base100_(rows, ctx) {
  var d0 = rows[0].prev, vMi = 100, vB = ctx.btcFrom && ctx.btcFrom <= d0 ? 100 : null;
  var out = { d: [d0], mi: [vMi], btc: [vB], a: {}, from: null }, cur = {};
  ctx.ids.forEach(function (id) { cur[id] = null; out.a[id] = [null]; });
  rows.forEach(function (r) {
    var det = r.mi && r.mi.detail, k = out.d.length;
    out.d.push(r.d);
    if (r.mi) vMi *= 1 + (r.mi.h || 0);
    out.mi.push(vMi);
    if (vB === null) { if (ctx.btcFrom && ctx.btcFrom <= r.d) vB = 100; }
    else if (r.btc) vB *= 1 + (r.btc.r || 0);
    out.btc.push(vB);
    if (det && out.from === null) out.from = r.prev;
    ctx.ids.forEach(function (id) {
      var x = det ? r.mi.a[id] : null;
      if (cur[id] === null && x && ctx.firstSeen[id] <= r.prev) { cur[id] = 100; out.a[id][k - 1] = 100; }   // arranca el día anterior en 100
      else if (cur[id] === null && out.from !== null && ctx.firstSeen[id] <= r.d && ctx.firstSeen[id] > r.prev) cur[id] = 100;   // entra en cartera ese día
      if (cur[id] !== null && x && x.r !== null && out.a[id][k - 1] !== null) cur[id] *= 1 + x.r;
      out.a[id].push(cur[id]);
    });
  });
  if (!out.btc.some(function (v) { return v !== null; })) out.btc = null;
  if (!rows.some(function (r) { return r.mi; })) out.mi = null;   // sin MyInvestor en la ventana: no hay línea que pintar
  return out;
}

/** Contribución acumulada: para cada día τ de la ventana, el enlace de Carino de [inicio, τ] (lo que dirían las barras si el periodo acabase ese día). */
function cumContrib_(rows, ids) {
  var out = { d: [rows[0].prev], mi: [0], res: [0], nod: [0], a: {} }, miRows = [];
  ids.forEach(function (id) { out.a[id] = [0]; });
  rows.forEach(function (r) {
    if (r.mi) miRows.push(r.mi);
    var L = linkMi_(miRows);
    out.d.push(r.d); out.mi.push(L.twr); out.res.push(L.res); out.nod.push(L.nod);
    ids.forEach(function (id) { out.a[id].push(L.c[id] || 0); });
  });
  return out;
}

/* ===================== Utilidades ===================== */

function norm_(v) {
  return String(v === null || v === undefined ? '' : v).toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/%/g, '').replace(/[^a-z0-9]+/g, '_').replace(/^_+/, '');
}
/** Clave de cabecera: normalizada y sin guiones bajos finales ('Target_%' -> 'target'). */
function hkey_(v) { return norm_(v).replace(/_+$/, ''); }
/** Clave sin sufijo de unidad ('total_eur' -> 'total', 'cantidad_btc' -> 'cantidad'). */
function hbase_(k) { return k.replace(/_(eur|euros|btc|usd|pct)$/, ''); }
function tabKey_(s) { return String(s).toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, ''); }

/** Columna de cada campo de `spec` en una fila de cabeceras ya normalizadas. */
function matchHeaders_(keys, spec) {
  var idx = {};
  Object.keys(spec).forEach(function (field) {
    var aliases = spec[field];
    for (var a = 0; a < aliases.length && !(field in idx); a++) {
      var alias = aliases[a], prefix = alias.charAt(alias.length - 1) === '*' ? alias.slice(0, -1) : null;
      for (var c = 0; c < keys.length; c++) {
        var k = keys[c]; if (!k) continue;
        var hit = prefix !== null ? (k.indexOf(prefix) === 0 && !/acum/.test(k)) : (k === alias || hbase_(k) === alias);
        if (hit) { idx[field] = c; break; }
      }
    }
  });
  return idx;
}

/**
 * Busca la fila de cabecera (primeras 60 filas) que contenga todos los campos de `required`
 * (un elemento puede ser una lista de alternativas). Las filas de texto anteriores se ignoran.
 */
function findTable_(values, spec, required) {
  if (!values || !values.length) return null;
  for (var i = 0; i < Math.min(values.length, 60); i++) {
    if (!values[i]) continue;
    var keys = values[i].map(hkey_), idx = matchHeaders_(keys, spec);
    var ok = required.every(function (f) { return Array.isArray(f) ? f.some(function (g) { return g in idx; }) : (f in idx); });
    if (ok) return makeTable_(values, i, keys, idx);
  }
  return null;
}
function makeTable_(values, headerRow, keys, idx) {
  var rows = values.slice(headerRow + 1).filter(function (r) { return r && r.some(function (c) { return c !== '' && c !== null && c !== undefined; }); });
  return {
    keys: keys, rows: rows,
    has: function (field) { return field in idx; },
    get: function (row, field) { return field in idx ? row[idx[field]] : null; }
  };
}

function isDate_(v) { return Object.prototype.toString.call(v) === '[object Date]' && !isNaN(v.getTime()); }
function isNum_(v) { return typeof v === 'number' && isFinite(v); }
function pad_(n) { return (n < 10 ? '0' : '') + n; }
function localKey_(d) { return d.getFullYear() + '-' + pad_(d.getMonth() + 1) + '-' + pad_(d.getDate()); }
function serialKey_(n) { return new Date(Date.UTC(1899, 11, 30) + Math.floor(n + 1e-7) * 86400000).toISOString().substring(0, 10); }

/**
 * Cualquier fecha -> 'yyyy-MM-dd': texto 'yyyy-mm-dd[ HH:mm]' (lo que pasa el adaptador), 'dd/mm/yyyy', número de serie
 * de Sheets u objeto Date (este último, con la hora local del entorno: en Apps Script el adaptador ya lo ha convertido).
 */
function toKey_(v) {
  if (v === null || v === undefined || v === '') return null;
  if (isDate_(v)) return localKey_(v);
  if (typeof v === 'number') return v > 20000 && v < 80000 ? serialKey_(v) : null;
  var s = String(v).trim(), m;
  if ((m = s.match(/^(\d{4})[-\/.](\d{1,2})[-\/.](\d{1,2})/))) return validKey_(+m[1], +m[2], +m[3]);
  if ((m = s.match(/^(\d{1,2})[\/.-](\d{1,2})[\/.-](\d{4})/))) return validKey_(+m[3], +m[2], +m[1]);
  if (/^\d{5}([.,]\d+)?$/.test(s)) { var n = parseFloat(s.replace(',', '.')); return n > 20000 && n < 80000 ? serialKey_(n) : null; }
  return null;
}
function validKey_(y, m, d) { return (m >= 1 && m <= 12 && d >= 1 && d <= 31) ? y + '-' + pad_(m) + '-' + pad_(d) : null; }

/** Hora del precio de la cripto: admite texto, Date o serial con decimales -> 'yyyy-MM-dd HH:mm' */
function timeLabel_(v) {
  if (v === null || v === undefined || v === '') return '';
  if (isDate_(v)) return localKey_(v) + ' ' + pad_(v.getHours()) + ':' + pad_(v.getMinutes());
  if (typeof v === 'number' && v > 20000 && v < 80000) {
    var mins = Math.round((v - Math.floor(v)) * 1440);
    return serialKey_(v) + ' ' + pad_(Math.floor(mins / 60) % 24) + ':' + pad_(mins % 60);
  }
  return str_(v);
}
function toIso_(d) { return isDate_(d) ? d.toISOString() : String(d); }
function keyMs_(key) { return Date.UTC(+key.substring(0, 4), +key.substring(5, 7) - 1, +key.substring(8, 10)); }
function daysBetween_(a, b) { return Math.round((keyMs_(b) - keyMs_(a)) / 86400000); }
function shiftDays_(key, n) { return new Date(keyMs_(key) + n * 86400000).toISOString().substring(0, 10); }
/** Mismo día n meses antes/después (si no existe, el último día de ese mes). */
function shiftMonths_(key, n) {
  var y = +key.substring(0, 4), m = +key.substring(5, 7) - 1 + n, d = +key.substring(8, 10);
  y += Math.floor(m / 12); m = ((m % 12) + 12) % 12;
  var last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
  return y + '-' + pad_(m + 1) + '-' + pad_(Math.min(d, last));
}
function maxKey_(keys) { var best = null; keys.forEach(function (k) { if (k && (!best || k > best)) best = k; }); return best; }
/** Índice del primer elemento con date >= key (−1 si no hay). */
function firstIndexGE_(arr, key) { for (var i = 0; i < arr.length; i++) if (arr[i].date >= key) return i; return -1; }
/** Índice del último elemento con date <= key (−1 si no hay). */
function lastIndexLE_(arr, key) { for (var i = arr.length - 1; i >= 0; i--) if (arr[i].date <= key) return i; return -1; }

function num_(v) {
  if (v === null || v === undefined || v === '') return null;
  if (typeof v === 'number') return isFinite(v) ? v : null;
  if (isDate_(v)) return null;
  var s = String(v).replace(/[€$\s ]/g, '').replace(/−/g, '-');
  if (!/\d/.test(s) || /^\d{4}-\d{1,2}-\d{1,2}/.test(s)) return null;   // una fecha no es un número
  if (/^-?\d{1,3}(\.\d{3})+(,\d+)?$/.test(s) || /^-?\d+,\d+$/.test(s)) s = s.replace(/\./g, '').replace(',', '.');
  var n = parseFloat(s.replace(/[^\d.\-eE]/g, ''));
  return isFinite(n) ? n : null;
}
/** Fracción (0.8). Si viene como 80 o '80%', lo pasa a 0.8. Para TER, >0.05 se interpreta como %. */
function pct_(v, isTer) {
  var hasPct = typeof v === 'string' && v.indexOf('%') >= 0;
  var n = num_(v); if (n === null) return null;
  if (hasPct) return n / 100;
  if (isTer) return n > 0.05 ? n / 100 : n;
  return n > 1.5 ? n / 100 : n;
}
function str_(v) { return v === null || v === undefined ? '' : String(v).trim(); }
function copy_(o) { var r = {}; Object.keys(o).forEach(function (k) { r[k] = o[k]; }); return r; }
function byDate_(a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; }
function normSource_(v) {
  var s = norm_(v);
  if (s.indexOf('broker') >= 0 || s.indexOf('oficial') >= 0) return 'broker';
  if (!s) return 'desconocida';
  return s.indexOf('public') >= 0 || s.indexOf('mercado') >= 0 ? 'public_markets' : s;
}
function sourceLabel_(s) { return s === 'broker' ? 'broker oficial' : s === 'public_markets' ? 'estimación de mercado' : (s || 'n/d'); }

/** Deja el objeto apto para JSON / google.script.run (sin Date, NaN ni Infinity). */
function sanitize_(o) {
  if (o === null || o === undefined) return null;
  if (isDate_(o)) return o.toISOString();
  if (typeof o === 'number') return isFinite(o) ? Math.round(o * 1e8) / 1e8 : null;
  if (Array.isArray(o)) return o.map(sanitize_);
  if (typeof o === 'object') { var r = {}; Object.keys(o).forEach(function (k) { r[k] = sanitize_(o[k]); }); return r; }
  return o;
}

#!/usr/bin/env node
/**
 * Arnés de preview y pruebas: ejecuta computeDashboard_ de Code.gs (la parte de cálculo puro, sin Google) con los CSV
 * de data/ (export real de la hoja; copia en tests/fixtures/) y genera data/mock.json, que es lo que pinta preview.html.
 *
 *   node harness.js                                   -> data/mock.json + resumen por consola
 *   node harness.js --now 2026-10-29T23:30:00+01:00   -> fija el «ahora» (antigüedad de los datos reproducible)
 *   node harness.js --data tests/fixtures             -> lee los CSV de otra carpeta
 *   node harness.js --sin Crypto,Crypto_Movs,Flujos --out data/mock_sin_pestanas.json
 *   node harness.js --fechas serial|iso|date -> cómo llegan las fechas (número de serie, texto ISO u objeto Date)
 *
 * Pestañas que carga si existe el CSV: Historico, "Stocks y Fondos", Stocks, Fondos, Fondos_Ref, Targets,
 * Flujos, Crypto y Crypto_Movs. Un CSV que no existe equivale a una pestaña que no existe.
 * También se puede usar como módulo desde tests/: require('../harness.js').
 */
process.env.TZ = 'Europe/Madrid';
const fs = require('fs'), path = require('path'), vm = require('vm');

const TABS = ['Historico', 'Stocks y Fondos', 'Stocks', 'Fondos', 'Fondos_Ref', 'Targets', 'Flujos', 'Crypto', 'Crypto_Movs'];
const DATE_COLS = ['fecha', 'nav_date', 'actualizado'];
const SHEET_URL = 'https://docs.google.com/spreadsheets/d/TU_SPREADSHEET_ID/edit';

function parseCSV(t) {
  const rows = []; let row = [], f = '', q = false;
  t = t.replace(/^\uFEFF/, '');
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    if (q) { if (c === '"') { if (t[i + 1] === '"') { f += '"'; i++; } else q = false; } else f += c; }
    else if (c === '"') q = true;
    else if (c === ',') { row.push(f); f = ''; }
    else if (c === '\r') { /* CRLF */ }
    else if (c === '\n') { row.push(f); rows.push(row); row = []; f = ''; }
    else f += c;
  }
  if (f || row.length) { row.push(f); rows.push(row); }
  return rows;
}

function serialToIso(n) { return new Date(Date.UTC(1899, 11, 30) + Math.floor(n) * 86400000).toISOString().slice(0, 10); }

/** Convierte las celdas como lo haría getValues(): números como number y fechas según `fechas`. */
function typed(rows, fechas) {
  let hdr = null;
  return rows.map(r => {
    const low = r.map(c => String(c).trim().toLowerCase());
    if (!hdr && (low.includes('fecha') || low.includes('isin'))) { hdr = low; return r; }
    return r.map((c, i) => {
      if (c === '') return '';
      const n = Number(c);
      if (isNaN(n) || !/^-?[\d.]+(e-?\d+)?$/i.test(c.trim())) return c;
      if (hdr && DATE_COLS.includes(hdr[i]) && n > 40000 && n < 80000) {
        if (fechas === 'serial') return n;
        if (fechas === 'iso') return serialToIso(n);
        return new Date(1899, 11, 30 + n);            // como una celda de fecha de Sheets
      }
      return n;
    });
  });
}

function loadTab(dir, name, fechas) {
  const file = path.join(dir, name + '.csv');
  if (!fs.existsSync(file)) return undefined;
  return typed(parseCSV(fs.readFileSync(file, 'utf8')), fechas || 'date');
}

/** { 'Historico': [[...]], ... } con los CSV que existan en `dir` (menos los de `sin`). */
function loadTabs(opts) {
  opts = opts || {};
  const dir = opts.dir || path.join(__dirname, 'data'), sin = opts.sin || [], tabs = {};
  TABS.forEach(n => {
    if (sin.includes(n)) return;
    const v = loadTab(dir, n, opts.fechas);
    if (v !== undefined) tabs[n] = v;
  });
  return tabs;
}

/** Carga Code.gs en un contexto aislado (sin SpreadsheetApp, Utilities ni nada de Google). */
function loadCode(file) {
  const ctx = { console };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(file || path.join(__dirname, 'Code.gs'), 'utf8'), ctx, { filename: 'Code.gs' });
  return ctx;
}

function compute(tabs, opts) {
  const ctx = loadCode();
  const out = ctx.computeDashboard_(tabs, Object.assign({ tz: 'Europe/Madrid', now: new Date() }, opts || {}));
  const plain = JSON.parse(JSON.stringify(out));
  plain.spreadsheetUrl = SHEET_URL;
  return plain;
}

module.exports = { TABS, parseCSV, typed, loadTab, loadTabs, loadCode, compute, SHEET_URL };

if (require.main === module) {
  const a = process.argv.slice(2), arg = k => { const i = a.indexOf(k); return i >= 0 ? a[i + 1] : null; };
  const tabs = loadTabs({ dir: arg('--data') || undefined, sin: (arg('--sin') || '').split(',').filter(Boolean), fechas: arg('--fechas') || 'date' });
  const out = compute(tabs, { now: arg('--now') ? new Date(arg('--now')) : new Date() });
  const file = arg('--out') || path.join(__dirname, 'data', 'mock.json');
  fs.writeFileSync(file, JSON.stringify(out, null, 1));
  const e = v => v === null || v === undefined ? 'n/d' : v.toFixed(2);
  const p = v => v === null || v === undefined ? 'n/d' : (v * 100).toFixed(2) + '%';
  console.log('Pestañas cargadas:', Object.keys(tabs).join(', ') || '(ninguna)');
  console.log('PATRIMONIO TOTAL', e(out.total.patrimonio), '= MyInvestor', e(out.total.myinvestor), '(' + out.total.miDate + ') + cripto', e(out.total.cripto), '(' + out.total.crDate + ')');
  console.log('Peso cripto sobre el total:', p(out.total.pesoCripto));
  out.perf.periods.forEach(q => console.log(' ', q.label.padEnd(13),
    ['total', 'mi', 'cr', 'bench'].map(k => k + ' ' + (q[k] ? p(q[k].twr) + (q[k].partial ? '*' : '') + ' [' + e(q[k].gain) + ' €, ' + q[k].from + '→' + q[k].to + ']' : 'n/d')).join(' | ')));
  if (out.crypto.available) console.log('Cripto:', out.crypto.cantidad, out.crypto.activo, '@', out.crypto.precio, '=', e(out.crypto.valor), '· coste', e(out.crypto.coste), '(' + out.crypto.costeOrigen + ') · plusvalía', e(out.crypto.plusvalia),
    '· sin coste', out.crypto.sinCoste ? out.crypto.sinCoste.btc + ' BTC (' + out.crypto.sinCoste.origen + ')' : 'n/d');
  else console.log('Cripto: no disponible —', out.crypto.reason);
  console.log('Posiciones:', out.mi.positions.length, '· asignación:', out.mi.allocation.map(x => x.key + ' ' + p(x.actual)).join(' / '));
  console.log('Estado:', out.status.map(s => s.tab + (s.found ? ' ' + s.rows : ' ✗')).join(' · '));
  if (out.warnings.length) console.log('Avisos:\n - ' + out.warnings.join('\n - '));
  console.log('→', path.relative(process.cwd(), file));
}

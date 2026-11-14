/** Calcula y valida la copia local a partir de las celdas de Sheets recibidas por stdin. */
const { readFileSync } = require('node:fs');
const { compute } = require('./harness');

const wealthTabs = new Set(['historico', 'crypto', 'cripto', 'flujos', 'cryptomovs', 'criptomovs']);
const wealthLabels = new Set(['Historico', 'Crypto', 'Flujos', 'Crypto_Movs']);
const formulaError = /^#(?:REF!|N\/A|VALUE!|DIV\/0!|NUM!|NAME\?|NULL!|ERROR!)$/;

function invalid(message) {
  process.stderr.write(message + '\n');
  process.exitCode = 2;
}

function main() {
  const { tabs, zone } = JSON.parse(readFileSync(0, 'utf8'));
  for (const [title, rows] of Object.entries(tabs)) {
    const key = title.toLowerCase().normalize('NFD').replace(/[^a-z0-9]/g, '');
    if (!Array.isArray(rows) || rows.some(row => !Array.isArray(row))) {
      return invalid('Google Sheets devolvió una pestaña con un formato no válido.');
    }
    if (wealthTabs.has(key) && rows.some(row => row.some(cell => typeof cell === 'string' && formulaError.test(cell)))) {
      return invalid('Hay errores de fórmula en las valoraciones o los flujos de la hoja.');
    }
  }

  const now = new Date();
  const today = new Intl.DateTimeFormat('sv-SE', { timeZone: zone }).format(now);
  const portfolio = compute(tabs, { tz: zone, now, today });
  const { total, series, flujosPatrimonio, conciliacionBBVA, status } = portfolio;
  const validDate = (day) => typeof day === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(day)
    && !Number.isNaN(Date.parse(day)) && new Date(day).toISOString().slice(0, 10) === day;
  const valued = (key, carry) => series.filter(row => !row[carry] && Number.isFinite(row[key])).map(row => row.d);
  const aligned = (dates, flows) => Array.isArray(flows) && flows.length === dates.length
    && flows.every((flow, i) => flow.fecha === dates[i] && (flow.neto === null || Number.isFinite(flow.neto)));
  const miDates = valued('mi', 'miFill');
  const crDates = valued('cr', 'crFill');
  if (!Number.isFinite(total.myinvestor) || !Number.isFinite(total.cripto) || !miDates.length || !crDates.length
      || !series.every(row => validDate(row.d))) {
    return invalid('Historico y Crypto deben contener valoraciones con fechas válidas.');
  }
  if (status.some(item => wealthLabels.has(item.tab) && item.level === 'warn')
      || flujosPatrimonio.version !== 1 || !aligned(miDates, flujosPatrimonio.myinvestor)
      || !aligned(crDates, flujosPatrimonio.cripto) || conciliacionBBVA.version !== 1) {
    return invalid('Revisa las pestañas de flujos de cartera antes de sincronizar.');
  }
  process.stdout.write(JSON.stringify(portfolio));
}

main();

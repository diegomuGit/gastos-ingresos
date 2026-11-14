const { test } = require('node:test');
const assert = require('node:assert/strict');
const { compute } = require('../dashboard_myinvestor/harness.js');

test('incorporar cripto a la serie no se cuenta como rentabilidad de mercado', () => {
  const data = compute({ Historico: [['Fecha', 'Total'], ['2026-01-01', 100], ['2026-01-02', 110]],
    Crypto: [['Fecha', 'Valor_EUR', 'Cantidad'], ['2026-01-02', 100, 1]] });
  assert.equal(data.total.patrimonio, 210);
  const period = data.perf.periods.find(p => p.key === '1d');
  assert.equal(period.total.gain, 10);
  assert.equal(period.total.flows, 100);
  assert.equal(data.crypto.coste, null);
  assert.equal(data.crypto.plusvalia, null);
});

test('el coste de una valoración no incluye las compras posteriores', () => {
  const data = compute({ Crypto: [['Fecha', 'Valor_EUR', 'Cantidad'], ['2026-01-02', 150, 1]],
    Crypto_Movs: [['Fecha', 'Tipo', 'Cantidad_BTC', 'Total_EUR'], ['2026-01-01', 'Compra', 1, 100], ['2026-01-03', 'Compra', 1, 200]] });
  assert.equal(data.crypto.coste, 100);
  assert.equal(data.crypto.costeFecha, '2026-01-01');
  assert.equal(data.crypto.plusvalia, 50);
});

test('una valoración nueva sin coste no hereda el coste de una posición antigua', () => {
  const data = compute({ Crypto: [['Fecha', 'Valor_EUR', 'Cantidad', 'Coste_EUR'],
    ['2026-01-01', 100, 1, 80], ['2026-01-02', 250, 2, '']] });
  assert.equal(data.crypto.series[0].coste, 80);
  assert.equal(data.crypto.series[1].coste, null);
  assert.equal(data.crypto.coste, null);
});

test('el catálogo usa las aportaciones que ya se descontaron en rentabilidad', () => {
  const data = compute({ Historico: [['Fecha', 'Total', 'Aportaciones'], ['2026-01-01', 1000, 0], ['2026-01-03', 1260, 250]],
    Flujos: [['Fecha', 'Tipo', 'Importe', 'Nota'], ['2026-01-03', 'Aportación', 250, 'BBVA']] });
  const [flow] = data.conciliacionBBVA.aportaciones;
  assert.equal(data.conciliacionBBVA.aportaciones.length, 1);
  assert.equal(flow.clave, data.mi.flows.items[0].claveBBVA);
  assert.equal(flow.centimos, 25000);
  assert.equal(data.mi.flows.total, 250);
  const period = data.perf.periods.find(p => p.key === 'inicio');
  assert.equal(period.mi.flows, 250);
  assert.equal(period.mi.gain, 10);
  assert.equal(period.total.gain, 10);
  assert.ok(Math.abs(period.mi.twr - 0.01) < 1e-12);
});

test('el catálogo abarca el histórico y distingue aportaciones idénticas', () => {
  const lines = Array.from({ length: 101 }, () => ['2026-01-03', 'Aportación', 250]);
  const make = rows => compute({ Flujos: [['Fecha', 'Tipo', 'Importe'], ...rows, ['2026-01-03', 'Retirada', 250]] });
  const a = make(lines);
  const b = make([...lines, ['2026-01-04', 'Aportación', 10]]);
  assert.equal(a.mi.flows.items.length, 100);
  assert.equal(a.conciliacionBBVA.aportaciones.length, 101);
  const ids = a.conciliacionBBVA.aportaciones.map(f => f.clave);
  assert.equal(new Set(ids).size, 101);
  assert.deepEqual(b.conciliacionBBVA.aportaciones.slice(0, 101).map(f => f.clave), ids);
});

test('la exportación de flujos marca estimaciones y distingue cantidades desconocidas', () => {
  const data = compute({ Historico: [['Fecha', 'Total'], ['2026-01-01', 1000], ['2026-01-02', 1100]],
    Crypto: [['Fecha', 'Valor_EUR', 'Cantidad'], ['2026-01-01', 100, 1],
      ['2026-01-02', 150, 1.2], ['2026-01-03', 160, '']] });
  assert.equal(data.flujosPatrimonio.myinvestor, null);
  const [, estimated, unknown] = data.flujosPatrimonio.cripto;
  assert.equal(estimated.fecha, '2026-01-02');
  assert.ok(Math.abs(estimated.neto - 25) < 1e-9);
  assert.equal(estimated.estimado, true);
  assert.equal(unknown.neto, null);
});

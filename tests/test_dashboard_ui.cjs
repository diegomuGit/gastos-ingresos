const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

// Controlamos la red para entregar respuestas fuera de orden sin depender de su latencia.
function app() {
  const nodes = new Map();
  function node() {
    return {
      children: [], value: "", textContent: "", hidden: false, disabled: false,
      dataset: {}, style: {}, classList: { add() {} }, attributes: {},
      get firstChild() { return this.children[0]; },
      get options() { return this.children; },
      append(...items) { this.children.push(...items); },
      appendChild(item) { this.children.push(item); },
      removeChild(item) { this.children.splice(this.children.indexOf(item), 1); },
      setAttribute(key, value) { this.attributes[key] = value; },
      querySelectorAll() { return []; }, addEventListener() {},
    };
  }
  const document = {
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, node()); return nodes.get(id); },
    createElement: node, createElementNS: node, querySelectorAll() { return []; },
    querySelector: node,
  };
  const calls = [];
  const context = vm.createContext({
    document, URLSearchParams, AbortController, Intl, setTimeout, clearTimeout,
    window: { addEventListener() {} },
    fetch(url, options) {
      return new Promise((resolve, reject) => calls.push({ url, options, reject,
        reply(body) { resolve({ ok: true, json: async () => body }); } }));
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../bbva_gastos/web/app.js"), "utf8"), context);
  return { calls, document, run: (code) => vm.runInContext(code, context) };
}

function dashboard(mes, count = 1) {
  return {
    meses: { meses: [ { mes: "2026-01", completo: true }, { mes: "2026-02", completo: false } ] },
    kpis: { mes, movimientos: count, ingresos: "10.00", consumo: "1.00", ahorro: "0.00",
      resultado: "9.00", completo: false, sin_clasificar_n: 0, revisar_n: 0,
      consumo_medio_meses_completos: null },
    evolucion: [], categorias: { consumo: [], ingresos: [], aparte: [] },
    insights: { frases: [] }, lectura: { parrafos: [] },
    estado: { movimientos: count, pendientes: 0, desde: "2026-01-01", hasta: "2026-02-02" },
  };
}

function page(total) {
  return { movimientos: [], total, total_periodo: 10, offset: 0, limit: 50,
    resumen: { ingresos: "0.00", salidas: "0.00", neto: "0.00" } };
}

const tick = () => new Promise(resolve => setImmediate(resolve));

test("el último periodo elegido gana aunque la primera respuesta llegue tarde", async () => {
  const a = app();
  a.document.getElementById("mes").value = "2026-02";
  const current = a.run("cargar()");
  assert.equal(a.calls[0].options.signal.aborted, true);
  a.calls[1].reply(dashboard("2026-02"));
  await tick();
  a.calls[2].reply(page(1));
  await current;
  a.calls[0].reply(dashboard("todos", 99));
  await tick();
  assert.equal(a.document.getElementById("mes").value, "2026-02");
  assert.match(a.document.getElementById("rango").textContent, /febrero 2026 · 1 movimientos/);
  assert.equal(a.calls.length, 3);
});

test("las respuestas viejas de búsqueda no reemplazan los resultados nuevos", async () => {
  const a = app();
  a.calls[0].reply(dashboard("todos"));
  await tick();
  a.calls[1].reply(page(10));
  await tick();
  const old = a.run('tablaEstado.texto = "cafe"; cargarTabla()');
  const latest = a.run('tablaEstado.texto = "nomina"; cargarTabla()');
  assert.equal(a.calls[2].options.signal.aborted, true);
  a.calls[3].reply(page(2));
  await latest;
  a.calls[2].reply(page(8));
  await old;
  assert.equal(a.document.getElementById("cuenta").textContent, "2 de 10 movimientos");
});

test("una lectura pendiente no se dibuja en otro periodo", async () => {
  const a = app();
  a.calls[0].reply(dashboard("todos"));
  await tick();
  a.calls[1].reply(page(10));
  await tick();
  const reading = a.run("generarLectura()");
  a.document.getElementById("mes").value = "2026-02";
  const current = a.run("cargar()");
  a.calls[3].reply(dashboard("2026-02"));
  await tick();
  a.calls[4].reply(page(1));
  await current;
  a.calls[2].reply({ parrafos: ["LECTURA DEL PERIODO ANTERIOR"] });
  await reading;
  assert.notEqual(a.document.getElementById("lectura").children[0].textContent, "LECTURA DEL PERIODO ANTERIOR");
  assert.equal(a.document.getElementById("generar").disabled, false);
});

test("un fallo de carga oculta los datos anteriores y permite reintentar", async () => {
  const a = app();
  a.calls[0].reply(dashboard("todos"));
  await tick();
  a.calls[1].reply(page(10));
  await tick();
  const failed = a.run("cargar()");
  a.calls[2].reject(new Error("Sin conexión"));
  await failed;
  assert.equal(a.document.getElementById("panel").hidden, true);
  assert.match(a.document.getElementById("aviso").textContent, /Actualizar/);
  const retry = a.run("cargar()");
  a.calls[3].reply(dashboard("todos"));
  await tick();
  a.calls[4].reply(page(10));
  await retry;
  assert.equal(a.document.getElementById("panel").hidden, false);
  assert.equal(a.document.getElementById("aviso").hidden, true);
});

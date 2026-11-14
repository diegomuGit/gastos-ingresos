const mesesCortos = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const mesesLargos = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
const origenTexto = { traspaso: "Traspaso a MyInvestor", manual: "Manual", regla: "Regla", correccion: "Corrección", jev: "Jev", pendiente: "Pendiente" };
const coberturaTexto = { completa: "cobertura completa", parcial: "cobertura parcial", desconocida: "cobertura desconocida" };
let categoriasEditables = {};
let movimientoEditado = null;
const formato = new Intl.NumberFormat("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: true });
const formatoCorto = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 1, useGrouping: true });
const SVGNS = "http://www.w3.org/2000/svg";
const SERIES = [
  { id: "ingresos", nombre: "Ingresos", color: "var(--bbva-chart-1)" },
  { id: "consumo", nombre: "Consumo", color: "var(--bbva-chart-2)" },
  { id: "ahorro", nombre: "Ahorro", color: "var(--bbva-chart-3)" }
];

const seriesOn = { ingresos: true, consumo: true, ahorro: false };
let vista = "barras";
let serieCache = [];
let mesActivo = "";
let categoriasCache = [];
let movimientosCache = [];
const tablaEstado = { orden: "fecha", dir: "desc", texto: "", direccion: "", origen: "", categoria: "", revisar: false, limit: 50, offset: 0 };
let resizeTimer = 0;
let searchTimer = 0;
let cargaId = 0;
let tablaId = 0;
let cargaController;
let tablaController;
let periodoVisible = null;
let tablaDatos = null;
let generando = false;
let movimientosPeriodo = 0;

function euros(value, modo) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const cuerpo = formato.format(Math.abs(n)) + " €";
  if (modo === "salida") return (n === 0 ? "" : "−") + cuerpo;
  if (n > 0) return "+" + cuerpo;
  if (n < 0) return "−" + cuerpo;
  return cuerpo;
}

function limpiar(nodo) {
  while (nodo.firstChild) nodo.removeChild(nodo.firstChild);
}

async function pedir(ruta, metodo, signal) {
  const respuesta = await fetch(ruta, { method: metodo || "GET", cache: "no-store", signal });
  let cuerpo;
  try { cuerpo = await respuesta.json(); }
  catch { throw new Error("El servidor no devolvió una respuesta válida. Pulsa Actualizar para reintentar."); }
  if (!respuesta.ok) throw new Error(cuerpo.error || "No pude leer la base.");
  return cuerpo;
}

function nombreMes(mes) {
  const partes = mes.split("-");
  return mesesLargos[Number(partes[1]) - 1] + " " + partes[0];
}

function opcion(select, valor, etiqueta) {
  const nodo = document.createElement("option");
  nodo.value = valor;
  nodo.textContent = etiqueta;
  select.appendChild(nodo);
}

function tarjeta(etiqueta, cifra, claseCifra, nota, extra) {
  const articulo = document.createElement("article");
  articulo.className = "card";
  const p = document.createElement("p");
  p.className = "kpi-label";
  p.textContent = etiqueta;
  const h = document.createElement("p");
  h.className = "kpi-value num";
  if (claseCifra) h.classList.add(claseCifra);
  h.textContent = cifra;
  articulo.append(p, h);
  if (extra) articulo.append(extra);
  if (nota) {
    const n = document.createElement("p");
    n.className = "kpi-note";
    n.textContent = nota;
    articulo.append(n);
  }
  return articulo;
}

function variacion(pct) {
  if (pct === null || pct === undefined || pct === "") return null;
  const n = Number(pct);
  if (!Number.isFinite(n) || n === 0) return null;
  const p = document.createElement("p");
  p.className = "delta num " + (n > 0 ? "neg" : "pos");
  p.textContent = (n > 0 ? "+" : "−") + formato.format(Math.abs(n)) + " % vs. mes anterior";
  return p;
}

function pintarKpis(datos) {
  const host = document.getElementById("kpis");
  limpiar(host);
  const notaConsumo = datos.sin_clasificar_n
    ? datos.sin_clasificar_n + " sin clasificar, fuera de esta cifra"
    : "Sin el ahorro ni los Bizum a personas";
  const notaIngresos = datos.mes === "todos"
    ? "Nómina, Bizum, transferencias y devoluciones"
    : (datos.completo ? "Mes completo" : "Mes incompleto");
  const resultado = Number(datos.resultado);
  host.append(
    tarjeta("Ingresos", euros(datos.ingresos, "entrada"), "pos", notaIngresos),
    tarjeta("Consumo", euros(datos.consumo, "salida"), null, notaConsumo, variacion(datos.variacion_consumo)),
    tarjeta("Ahorro", euros(datos.ahorro, "salida"), null, datos.traspasos_myinvestor_n
      ? "Incluye " + euros(datos.traspasos_myinvestor, "salida") + " en traspasos conciliados con MyInvestor"
      : "No entra en el consumo"),
    tarjeta("Balance del periodo", euros(datos.resultado), resultado < 0 ? "neg" : "pos", "Entradas menos salidas; no es el saldo de la cuenta")
  );
}

function svgEl(nombre) {
  return document.createElementNS(SVGNS, nombre);
}

function ejeBonito(maximo) {
  const valor = Number(maximo);
  if (!Number.isFinite(valor) || valor <= 0) return 1;
  const exp = Math.pow(10, Math.floor(Math.log10(valor)));
  const fraccion = valor / exp;
  const bonito = fraccion <= 1 ? 1 : fraccion <= 2 ? 2 : fraccion <= 5 ? 5 : 10;
  return bonito * exp;
}

function textoEje(valor, tope) {
  if (!valor) return "0";
  if (tope >= 1000) return formatoCorto.format(valor / 1000) + " mil";
  return formatoCorto.format(valor);
}

function yDe(valor, tope, arriba, alto) {
  return arriba + alto - (Number(valor) / tope) * alto;
}

function pintarEvolucion(serie, mesSeleccionado) {
  const host = document.getElementById("evolucion");
  limpiar(host);
  document.getElementById("tip").hidden = true;
  if (!serie.length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "Sin movimientos en la base.";
    host.append(p);
    return;
  }
  const activas = SERIES.filter((item) => seriesOn[item.id]);
  if (!activas.length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "Activa al menos una serie.";
    host.append(p);
    return;
  }
  const ancho = Math.max(host.clientWidth || 720, serie.length * 64, 320);
  const altoSvg = 280;
  const padL = 64;
  const padR = 12;
  const padT = 16;
  const padB = 36;
  const plotH = altoSvg - padT - padB;
  const plotW = ancho - padL - padR;
  const maximo = Math.max(1, ...serie.map((fila) => Math.max(...activas.map((item) => Number(fila[item.id]) || 0))));
  const tope = ejeBonito(maximo * 1.05);
  const svg = svgEl("svg");
  svg.setAttribute("viewBox", "0 0 " + ancho + " " + altoSvg);
  svg.setAttribute("width", String(ancho));
  svg.setAttribute("height", String(altoSvg));
  svg.setAttribute("role", "group");
  svg.setAttribute("aria-label", "Evolución mensual");
  const slot = plotW / serie.length;
  for (const tick of [0, tope / 2, tope]) {
    const y = yDe(tick, tope, padT, plotH);
    const linea = svgEl("line");
    linea.setAttribute("x1", String(padL));
    linea.setAttribute("x2", String(ancho - padR));
    linea.setAttribute("y1", String(y));
    linea.setAttribute("y2", String(y));
    linea.setAttribute("stroke", "var(--bbva-chart-grid)");
    svg.append(linea);
    const etiqueta = svgEl("text");
    etiqueta.setAttribute("x", String(padL - 8));
    etiqueta.setAttribute("y", String(y + 4));
    etiqueta.setAttribute("text-anchor", "end");
    etiqueta.textContent = textoEje(tick, tope);
    svg.append(etiqueta);
  }
  serie.forEach((fila, indice) => {
    const x0 = padL + slot * indice;
    const centro = x0 + slot / 2;
    if (mesSeleccionado && fila.mes === mesSeleccionado) {
      const fondo = svgEl("rect");
      fondo.setAttribute("x", String(x0));
      fondo.setAttribute("y", "8");
      fondo.setAttribute("width", String(slot));
      fondo.setAttribute("height", String(altoSvg - 8));
      fondo.setAttribute("fill", "var(--bbva-sand)");
      svg.append(fondo);
    }
    if (vista === "barras" && !fila.sin_datos) {
      const n = activas.length;
      const hueco = 4;
      const anchoGrupo = Math.min(slot * 0.76, n * 18 + (n - 1) * hueco);
      const anchoBarra = (anchoGrupo - (n - 1) * hueco) / n;
      const inicio = centro - anchoGrupo / 2;
      const base = yDe(0, tope, padT, plotH);
      activas.forEach((item, i) => {
        const valor = Number(fila[item.id]) || 0;
        const y = yDe(valor, tope, padT, plotH);
        const barra = svgEl("rect");
        barra.setAttribute("x", String(inicio + i * (anchoBarra + hueco)));
        barra.setAttribute("y", String(y));
        barra.setAttribute("width", String(Math.max(anchoBarra, 2)));
        barra.setAttribute("height", String(Math.max(base - y, 0)));
        barra.setAttribute("rx", "4");
        barra.setAttribute("fill", item.color);
        svg.append(barra);
      });
    }
    const hit = svgEl("rect");
    hit.setAttribute("x", String(x0));
    hit.setAttribute("y", "0");
    hit.setAttribute("width", String(slot));
    hit.setAttribute("height", String(altoSvg - 28));
    hit.setAttribute("fill", "transparent");
    hit.setAttribute("data-mes", fila.mes);
    hit.setAttribute("tabindex", "0");
    hit.setAttribute("role", "button");
    const corto = mesesCortos[Number(fila.mes.slice(5)) - 1];
    hit.setAttribute("aria-label", nombreMes(fila.mes) + (fila.completo ? "" : ", " + (coberturaTexto[fila.cobertura] || "incompleto")));
    svg.append(hit);
    const etiqueta = svgEl("text");
    etiqueta.setAttribute("x", String(centro));
    etiqueta.setAttribute("y", String(altoSvg - 12));
    etiqueta.setAttribute("text-anchor", "middle");
    const muestraAnio = indice === 0 || fila.mes.slice(0, 4) !== serie[indice - 1].mes.slice(0, 4);
    etiqueta.textContent = corto + (muestraAnio ? " " + fila.mes.slice(2, 4) : "") + (fila.completo ? "" : " ·");
    if (!fila.completo) etiqueta.setAttribute("class", "eje-incompleto");
    else if (fila.mes === mesSeleccionado) etiqueta.setAttribute("class", "eje-activo");
    svg.append(etiqueta);
  });
  if (vista === "lineas") {
    for (const item of activas) {
      const puntos = serie.map((fila, indice) => {
        if (fila.sin_datos) return null;
        const x = padL + slot * indice + slot / 2;
        const y = yDe(Number(fila[item.id]) || 0, tope, padT, plotH);
        return x + "," + y;
      });
      const segmentos = [[]];
      for (const punto of puntos) {
        if (punto) segmentos.at(-1).push(punto);
        else if (segmentos.at(-1).length) segmentos.push([]);
      }
      for (const tramo of segmentos.filter(tramo => tramo.length)) {
        const trazo = svgEl("polyline");
        trazo.setAttribute("fill", "none");
        trazo.setAttribute("stroke", item.color);
        trazo.setAttribute("stroke-width", item.id === "ingresos" ? "2.5" : "2");
        trazo.setAttribute("stroke-linejoin", "round");
        trazo.setAttribute("stroke-linecap", "round");
        trazo.setAttribute("points", tramo.join(" "));
        svg.append(trazo);
        const elegido = puntos[serie.findIndex(fila => fila.mes === mesSeleccionado)];
        for (const elegidoPunto of new Set([tramo.at(-1), ...(tramo.includes(elegido) ? [elegido] : [])])) {
          const [x, y] = elegidoPunto.split(",");
          const marca = svgEl("circle");
          marca.setAttribute("cx", x);
          marca.setAttribute("cy", y);
          marca.setAttribute("r", "3.5");
          marca.setAttribute("fill", item.color);
          svg.append(marca);
        }
      }
    }
    const hits = svg.querySelectorAll("[data-mes]");
    hits.forEach((hit) => svg.append(hit));
  }
  host.append(svg);
}

function mostrarTip(evento, fila) {
  const tip = document.getElementById("tip");
  limpiar(tip);
  const titulo = document.createElement("p");
  titulo.className = "tip-titulo";
  titulo.textContent = nombreMes(fila.mes) + (fila.completo ? "" : " · " + (coberturaTexto[fila.cobertura] || "incompleto"));
  tip.append(titulo);
  for (const item of SERIES) {
    if (!seriesOn[item.id]) continue;
    const linea = document.createElement("p");
    const modo = item.id === "ingresos" ? "entrada" : "salida";
    linea.textContent = item.nombre + " " + (fila.sin_datos ? "sin datos" : euros(fila[item.id], modo));
    tip.append(linea);
  }
  tip.hidden = false;
  const host = document.querySelector(".chart-wrap").getBoundingClientRect();
  const origen = evento.currentTarget && evento.currentTarget.getBoundingClientRect
    ? evento.currentTarget.getBoundingClientRect()
    : null;
  let x = origen ? origen.left + origen.width / 2 - host.left : evento.clientX - host.left;
  let y = origen ? origen.top - host.top + 8 : evento.clientY - host.top;
  tip.style.left = "0px";
  tip.style.top = "0px";
  const caja = tip.getBoundingClientRect();
  if (x + caja.width > host.width) x = host.width - caja.width - 8;
  if (y + caja.height > host.height) y = host.height - caja.height - 8;
  tip.style.left = Math.max(8, x) + "px";
  tip.style.top = Math.max(8, y) + "px";
}

function pintarCategorias(lista, activa) {
  const host = document.getElementById("categorias");
  limpiar(host);
  if (!lista.length) {
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = "No hay consumo clasificado en este periodo.";
    host.append(p);
    return;
  }
  const max = Math.max(...lista.map((fila) => Math.abs(Number(fila.importe))), 1);
  for (const fila of lista) {
    const boton = document.createElement("button");
    boton.type = "button";
    boton.className = "hbar-btn";
    boton.dataset.categoria = fila.categoria;
    boton.setAttribute("aria-pressed", activa === fila.categoria ? "true" : "false");
    const nombre = document.createElement("span");
    nombre.className = "hbar-name";
    nombre.textContent = fila.etiqueta;
    const pista = document.createElement("span");
    pista.className = "hbar-track";
    const barra = document.createElement("span");
    barra.className = "hbar";
    barra.style.width = Math.round(Math.abs(Number(fila.importe)) / max * 100) + "%";
    pista.append(barra);
    const valor = document.createElement("span");
    valor.className = "hbar-value num";
    valor.textContent = euros(Math.abs(Number(fila.importe)), "salida");
    boton.append(nombre, pista, valor);
    host.append(boton);
  }
}

function pintarInsights(frases) {
  const lista = document.getElementById("insights");
  limpiar(lista);
  for (const frase of frases) {
    const li = document.createElement("li");
    li.textContent = frase;
    lista.append(li);
  }
}

function pintarLectura(datos) {
  const host = document.getElementById("lectura");
  limpiar(host);
  const modelo = document.getElementById("modelo");
  const boton = document.getElementById("generar");
  if (datos.parrafos && datos.parrafos.length) {
    modelo.textContent = "Modelo " + (datos.modelo || "");
    for (const frase of datos.parrafos) {
      const p = document.createElement("p");
      p.textContent = frase;
      host.append(p);
    }
    boton.textContent = "Volver a generar";
    boton.dataset.nuevo = "1";
  } else {
    modelo.textContent = "";
    const p = document.createElement("p");
    p.className = "empty";
    p.textContent = movimientosPeriodo ? "Genera un resumen de los totales de este periodo." : "Carga un extracto para generar una lectura.";
    host.append(p);
    boton.textContent = "Generar lectura";
    boton.dataset.nuevo = "0";
  }
  boton.disabled = generando || !movimientosPeriodo || periodoVisible === null;
  if (generando) boton.textContent = "Generando…";
}

function marcarOrden() {
  document.querySelectorAll("th[data-col]").forEach((th) => {
    if (th.dataset.col !== tablaEstado.orden) {
      th.setAttribute("aria-sort", "none");
      return;
    }
    th.setAttribute("aria-sort", tablaEstado.dir === "asc" ? "ascending" : "descending");
  });
}

function pintarTabla() {
  const cuerpo = document.getElementById("tabla");
  const visibles = movimientosCache;
  const cuenta = document.getElementById("cuenta");
  const datos = tablaDatos;
  cuenta.textContent = datos.total + " de " + datos.total_periodo + " movimientos";
  document.getElementById("resumen-filtro").textContent = "Total del filtro · Entradas " + euros(datos.resumen.ingresos)
    + " · Salidas " + euros(datos.resumen.salidas, "salida") + " · Neto " + euros(datos.resumen.neto);
  document.getElementById("pagina").textContent = datos.total
    ? (datos.offset + 1) + "–" + (datos.offset + visibles.length) + " de " + datos.total
      + " · Página " + (Math.floor(datos.offset / datos.limit) + 1) + " de " + Math.ceil(datos.total / datos.limit)
    : "Sin resultados";
  document.getElementById("anterior").disabled = datos.offset === 0;
  document.getElementById("siguiente").disabled = datos.offset + visibles.length >= datos.total;
  document.getElementById("exportar").disabled = !datos.total;
  marcarOrden();
  limpiar(cuerpo);
  if (!visibles.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.className = "empty";
    td.textContent = datos.total_periodo ? "No hay coincidencias. Prueba otra búsqueda o limpia los filtros."
      : "No hay movimientos en este periodo. Carga un extracto PDF, Excel o CSV y pulsa Actualizar.";
    tr.append(td);
    cuerpo.append(tr);
    return;
  }
  for (const fila of visibles) {
    const tr = document.createElement("tr");
    const fecha = document.createElement("td");
    fecha.className = "num";
    fecha.textContent = fila.fecha.split("-").reverse().join("/");
    const concepto = document.createElement("td");
    concepto.textContent = fila.concepto;
    if (fila.detalle) {
      const detalle = document.createElement("span");
      detalle.className = "detalle";
      detalle.textContent = fila.detalle;
      concepto.append(detalle);
    }
    const categoria = document.createElement("td");
    categoria.textContent = fila.etiqueta;
    if (!fila.traspaso_myinvestor) {
      const editar = document.createElement("button");
      editar.type = "button";
      editar.className = "chip edit-category";
      editar.textContent = "Editar";
      editar.setAttribute("aria-label", "Editar categoría de " + fila.concepto);
      editar.addEventListener("click", () => editarCategoria(fila));
      categoria.append(editar);
    }
    const origen = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = "badge" + (fila.origen === "pendiente" || fila.revisar ? " revisar" : "");
    badge.textContent = (origenTexto[fila.origen] || fila.origen) + (fila.revisar ? " · revisar" : "");
    origen.append(badge);
    const importe = document.createElement("td");
    importe.className = "num " + (Number(fila.importe) < 0 ? "neg" : "pos");
    importe.textContent = euros(fila.importe);
    tr.append(fecha, concepto, categoria, origen, importe);
    cuerpo.append(tr);
  }
}

function rellenarFiltroCategoria(filas) {
  const select = document.getElementById("filtro-categoria");
  const previo = tablaEstado.categoria;
  limpiar(select);
  opcion(select, "", "Todas");
  const mapa = new Map();
  for (const fila of filas) mapa.set(fila.categoria, fila.etiqueta);
  const ordenadas = Array.from(mapa.entries()).sort((a, b) => a[1].localeCompare(b[1], "es"));
  for (const par of ordenadas) opcion(select, par[0], par[1]);
  select.value = mapa.has(previo) || previo === "" ? previo : "";
  tablaEstado.categoria = select.value;
}

function pintarRango(datos, kpisDatos) {
  const nodo = document.getElementById("rango");
  if (!datos.movimientos) {
    nodo.textContent = "La base está vacía. Carga un PDF o un Excel.";
    return;
  }
  if (kpisDatos && kpisDatos.mes && kpisDatos.mes !== "todos") {
    let frase = nombreMes(kpisDatos.mes) + " · " + kpisDatos.movimientos + " movimientos";
    frase += " · " + (coberturaTexto[kpisDatos.cobertura] || (kpisDatos.completo ? "mes completo" : "mes incompleto"));
    nodo.textContent = frase;
    return;
  }
  let frase = "Del " + datos.desde + " al " + datos.hasta + " · " + datos.movimientos + " movimientos";
  if (datos.pendientes) frase += " · " + datos.pendientes + " sin clasificar";
  nodo.textContent = frase;
}

function pintarPie(datos) {
  const nodo = document.getElementById("pie");
  const rango = datos.desde ? " Datos del " + datos.desde + " al " + datos.hasta + "." : "";
  nodo.textContent = "Tus movimientos se guardan en este equipo." + rango
    + " Una corrección tuya gana a Jev."
    + " El ahorro y los Bizum a personas no entran en el consumo.";
}

function pintarCobertura(datos) {
  const titulo = document.getElementById("cobertura-titulo");
  const detalle = document.getElementById("cobertura-detalle");
  if (!datos.movimientos && (!datos.cobertura || datos.cobertura === 'desconocida')) {
    titulo.textContent = "Empieza con tu primer extracto";
    detalle.textContent = "Guarda un PDF, Excel o CSV en entrada/ y ejecuta python -m bbva_gastos entrada. Después pulsa Actualizar.";
  } else {
    titulo.textContent = datos.sin_clasificar_n ? datos.sin_clasificar_n + " movimientos sin clasificar" : "Cobertura del periodo";
    const notas = [];
    if (datos.sin_clasificar_n) notas.push("Los pendientes quedan fuera del consumo y pueden cambiar los totales al clasificarlos.");
    if (datos.revisar_n) notas.push(datos.revisar_n + " movimientos requieren revisión.");
    if (datos.cobertura === 'desconocida') notas.push("No se ha declarado el intervalo de los extractos de este periodo.");
    else if (!datos.completo) notas.push("No hay cobertura para todo el periodo. Solo se comparan meses completos.");
    else notas.push("Los extractos cubren todo el periodo.");
    if (datos.consumo_medio_meses_completos !== null) notas.push("Consumo medio por mes completo: " + euros(datos.consumo_medio_meses_completos, "salida") + ".");
    if (!notas.length) notas.push("El consumo excluye ahorro y Bizum a personas.");
    detalle.textContent = notas.join(" ");
  }
  document.getElementById("ver-revision").hidden = !datos.revisar_n;
}

function pintarExtractos(extractos) {
  const lista = document.getElementById("extractos-lista");
  limpiar(lista);
  document.getElementById("extractos").hidden = !extractos.length;
  for (const extracto of extractos) {
    const item = document.createElement("li");
    item.textContent = extracto.nombre + " · " + (extracto.desde ? extracto.desde + " → " + extracto.hasta : "cobertura desconocida");
    lista.append(item);
  }
}

async function guardarCambio(path, data) {
  const respuesta = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  const resultado = await respuesta.json();
  if (!respuesta.ok) throw new Error(resultado.error || "No se pudo guardar el cambio.");
}

async function cargarTraspasos() {
  const panel = document.getElementById("traspasos");
  if (!panel.open || periodoVisible === null) return;
  const id = cargaId;
  const estado = document.getElementById("traspasos-estado");
  const lista = document.getElementById("traspasos-lista");
  estado.textContent = "Buscando coincidencias…";
  limpiar(lista);
  try {
    const datos = await pedir("/api/traspasos?mes=" + encodeURIComponent(periodoVisible), "GET", cargaController.signal);
    if (id !== cargaId) return;
    pintarTraspasos(datos);
  } catch (error) {
    if (id === cargaId && error.name !== "AbortError") estado.textContent = error.message;
  }
}

function pintarTraspasos(datos) {
  const lista = document.getElementById("traspasos-lista");
  const estado = document.getElementById("traspasos-estado");
  limpiar(lista);
  const n = datos.candidatos.length;
  const d = datos.decisiones.length;
  estado.textContent = datos.aviso || (n || d
    ? `${n} ${n === 1 ? 'coincidencia pendiente' : 'coincidencias pendientes'} · ${d} ${d === 1 ? 'decisión guardada' : 'decisiones guardadas'}`
    : "No hay coincidencias en este periodo.");

  function agregar(par, destino) {
    const caja = document.createElement("article");
    caja.className = "traspaso-fila";
    const titulo = document.createElement("strong");
    titulo.textContent = par.estado === "confirmado" ? (par.verificado ? "Traspaso confirmado" : "Traspaso confirmado · revisar fuente")
      : par.estado === "descartado" ? "Coincidencia descartada"
      : par.ambigua ? "Hay más de una coincidencia posible" : "Posible traspaso";
    caja.append(titulo);
    const fuentes = document.createElement("div");
    fuentes.className = "traspaso-fuentes";
    for (const [fuente, dato, detalle] of [["Salida BBVA", par.banco, [par.banco.concepto, par.banco.detalle]],
      ["Aportación MyInvestor", par.aporte, [par.aporte.origen, par.aporte.nota]]]) {
      const lado = document.createElement("div");
      const principal = document.createElement("p");
      principal.textContent = `${fuente} · ${dato.fecha} · ${euros(dato.importe)}`;
      const texto = document.createElement("p");
      texto.className = "hint";
      texto.textContent = detalle.filter(Boolean).join(" · ");
      lado.append(principal, texto);
      fuentes.append(lado);
    }
    caja.append(fuentes);
    const nota = document.createElement("p");
    nota.className = "hint";
    nota.setAttribute("role", "status");
    if (!par.estado) nota.textContent = par.dias ? `Diferencia de ${par.dias} ${par.dias === 1 ? 'día' : 'días'}.` : "Ambos apuntes son del mismo día.";
    else if (par.estado === "confirmado" && !par.verificado) nota.textContent = "El apunte ha cambiado o la aportación no aparece en la exportación actual. Revisa el enlace y deshazlo si ya no corresponde.";
    const controles = document.createElement("div");
    controles.className = "controls";

    function accion(etiqueta, tipo) {
      const boton = document.createElement("button");
      boton.type = "button";
      boton.className = tipo === "confirmar" ? "btn" : "chip";
      boton.textContent = etiqueta;
      boton.addEventListener("click", async () => {
        for (const button of lista.querySelectorAll("button")) button.disabled = true;
        try {
          await guardarCambio("/api/conciliar-traspaso", { banco: par.banco.hash, aporte: par.aporte.id, accion: tipo });
          await cargar();
        } catch (error) {
          nota.textContent = error.message;
          for (const button of lista.querySelectorAll("button")) button.disabled = false;
        }
      });
      controles.append(boton);
    }
    if (par.estado) accion("Deshacer decisión", "deshacer");
    else {
      accion("Confirmar traspaso", "confirmar");
      accion("No coinciden", "descartar");
    }
    caja.append(nota, controles);
    destino.append(caja);
  }

  for (const par of datos.candidatos) agregar(par, lista);
  if (datos.decisiones.length) {
    const historial = document.createElement("details");
    const titulo = document.createElement("summary");
    titulo.textContent = "Decisiones guardadas";
    historial.append(titulo);
    for (const par of datos.decisiones) agregar(par, historial);
    lista.append(historial);
  }
}

function editarCategoria(fila) {
  movimientoEditado = fila;
  const selector = document.getElementById("editar-valor");
  limpiar(selector);
  opcion(selector, "", "Usar clasificación automática");
  for (const [id, nombre] of Object.entries(categoriasEditables[fila.direccion] || {})) opcion(selector, id, nombre);
  selector.value = fila.categoria_manual || "";
  document.getElementById("editar-concepto").textContent = fila.concepto + " · " + euros(fila.importe);
  document.getElementById("editar-error").textContent = "";
  document.getElementById("editar-categoria").showModal();
}

function pintarConciliacion(datos) {
  const panel = document.getElementById("conciliacion");
  const lista = document.getElementById("conciliacion-lista");
  limpiar(lista);
  if (!datos) return;
  panel.hidden = !datos.candidatos.length && !datos.decisiones.length;
  const n = datos.candidatos.length;
  const d = datos.decisiones.length;
  document.getElementById("conciliacion-titulo").textContent = `Conciliación · ${n} ${n === 1 ? 'posible duplicado' : 'posibles duplicados'} · ${d} ${d === 1 ? 'decisión' : 'decisiones'}`;

  function fila(primero, segundo, decision) {
    const box = document.createElement("div");
    box.className = "conciliacion-fila";
    const nota = document.createElement("p");
    for (const [i, mov] of [primero, segundo].entries()) {
      const p = document.createElement("p");
      const fuente = mov.lector === 'enable-banking' ? 'Banco' : 'Extracto';
      const origen = mov.fichero ? fuente === 'Banco' ? ' · Cuenta •' + mov.fichero : ' · ' + mov.fichero : '';
      const saldo = mov.saldo ? ' · Saldo ' + euros(mov.saldo) : '';
      p.textContent = [i === 0 ? 'A' : 'B', fuente + origen, mov.fecha, euros(mov.importe), mov.concepto,
        mov.detalle].filter(Boolean).join(' · ') + saldo + (decision && decision.excluido === mov.hash ? ' · Excluido' : '');
      box.append(p);
    }
    const controls = document.createElement("div");
    controls.className = "controls";
    function accion(etiqueta, tipo, excluido = null) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chip";
      btn.textContent = etiqueta;
      btn.addEventListener("click", async () => {
        for (const button of controls.children) button.disabled = true;
        nota.textContent = "";
        try {
          await guardarCambio("/api/conciliar", { primero: primero.hash, segundo: segundo.hash, accion: tipo, excluido });
          await cargar();
        } catch (error) {
          nota.textContent = error.message;
          for (const button of controls.children) button.disabled = false;
        }
      });
      controls.append(btn);
    }
    if (decision) {
      accion(decision.excluido ? "Restaurar movimiento" : "Volver a revisar", "deshacer");
    } else {
      accion("Excluir A", "duplicado", primero.hash);
      accion("Excluir B", "duplicado", segundo.hash);
      accion("Son distintos", "distintos");
    }
    nota.setAttribute("role", "status");
    box.append(controls, nota);
    lista.append(box);
  }
  for (const par of datos.candidatos) fila(par.primero, par.segundo);
  for (const par of datos.decisiones) fila(par.primero, par.segundo, par);
}

function elegirMes(mes) {
  const select = document.getElementById("mes");
  select.value = select.value === mes ? "todos" : mes;
  cargar();
}

function elegirCategoria(categoria) {
  tablaEstado.categoria = tablaEstado.categoria === categoria ? "" : categoria;
  const select = document.getElementById("filtro-categoria");
  if (tablaEstado.categoria && !Array.from(select.options).some((item) => item.value === tablaEstado.categoria)) {
    opcion(select, categoria, categoria);
  }
  select.value = tablaEstado.categoria;
  pintarCategorias(categoriasCache, tablaEstado.categoria);
  cargarTabla();
}

function alOrdenar(clave) {
  if (tablaEstado.orden === clave) {
    tablaEstado.dir = tablaEstado.dir === "asc" ? "desc" : "asc";
  } else {
    tablaEstado.orden = clave;
    tablaEstado.dir = clave === "fecha" || clave === "importe" ? "desc" : "asc";
  }
  cargarTabla();
}

function parametrosTabla() {
  return new URLSearchParams({
    mes: periodoVisible || "todos", texto: tablaEstado.texto, direccion: tablaEstado.direccion,
    origen: tablaEstado.origen, categoria: tablaEstado.categoria, revisar: tablaEstado.revisar ? "1" : "0",
    orden: tablaEstado.orden, sentido: tablaEstado.dir, limit: tablaEstado.limit, offset: tablaEstado.offset
  });
}

function invalidarTabla() {
  clearTimeout(searchTimer);
  tablaId++;
  if (tablaController) tablaController.abort();
  document.getElementById("tabla-contenedor").setAttribute("aria-busy", "true");
  document.getElementById("cuenta").textContent = "Cargando movimientos…";
  document.getElementById("resumen-filtro").textContent = "Calculando el total del filtro…";
  document.getElementById("pagina").textContent = "";
  for (const id of ["anterior", "siguiente", "exportar"]) document.getElementById(id).disabled = true;
}

async function cargarTabla(reiniciar = true) {
  if (periodoVisible === null) return;
  invalidarTabla();
  if (reiniciar) tablaEstado.offset = 0;
  const id = tablaId;
  tablaController = new AbortController();
  try {
    const datos = await pedir("/api/movimientos?" + parametrosTabla(), "GET", tablaController.signal);
    if (id !== tablaId) return;
    tablaDatos = datos;
    tablaEstado.offset = datos.offset;
    movimientosCache = datos.movimientos;
    pintarTabla();
  } catch (error) {
    if (id !== tablaId || error.name === "AbortError") return;
    limpiar(document.getElementById("tabla"));
    document.getElementById("cuenta").textContent = "No se pudieron cargar los movimientos";
    document.getElementById("resumen-filtro").textContent = "Pulsa Actualizar para reintentar. " + error.message;
  } finally {
    if (id === tablaId) document.getElementById("tabla-contenedor").setAttribute("aria-busy", "false");
  }
}

async function cargar() {
  const id = ++cargaId;
  if (cargaController) cargaController.abort();
  cargaController = new AbortController();
  invalidarTabla();
  periodoVisible = null;
  document.getElementById("generar").disabled = true;
  document.getElementById("panel").hidden = true;
  const estadoCarga = document.getElementById("estado-carga");
  estadoCarga.textContent = "Cargando el dashboard…";
  const aviso = document.getElementById("aviso");
  aviso.hidden = true;
  const mes = document.getElementById("mes").value || "todos";
  const consulta = "mes=" + encodeURIComponent(mes);
  try {
    const datos = await pedir("/api/dashboard?" + consulta, "GET", cargaController.signal);
    if (id !== cargaId) return;
    periodoVisible = mes;
    const select = document.getElementById("mes");
    limpiar(select);
    opcion(select, "todos", "Todo el periodo");
    for (const item of datos.meses.meses.slice().reverse()) {
      opcion(select, item.mes, nombreMes(item.mes) + (item.completo ? "" : " · " + (coberturaTexto[item.cobertura] || "incompleto")));
    }
    if (mes !== "todos" && !datos.meses.meses.some((item) => item.mes === mes)) opcion(select, mes, nombreMes(mes) + " · sin movimientos");
    select.value = mes;
    mesActivo = mes === "todos" ? "" : mes;
    serieCache = datos.evolucion;
    categoriasCache = datos.categorias.consumo;
    movimientosPeriodo = datos.kpis.movimientos;
    document.getElementById("panel").hidden = false;
    pintarKpis(datos.kpis);
    pintarCobertura(datos.kpis);
    pintarExtractos(datos.meses.extractos || []);
    categoriasEditables = datos.categorias_editables || {};
    pintarConciliacion(datos.conciliacion);
    pintarEvolucion(serieCache, mesActivo);
    pintarInsights(datos.insights.frases);
    pintarLectura(datos.lectura);
    rellenarFiltroCategoria([...datos.categorias.consumo, ...datos.categorias.ingresos, ...datos.categorias.aparte]);
    pintarCategorias(categoriasCache, tablaEstado.categoria);
    pintarRango(datos.estado, datos.kpis);
    pintarPie(datos.estado);
    estadoCarga.textContent = "";
    if (typeof CustomEvent === "function") document.dispatchEvent(new CustomEvent("finanzas:actualizado"));
    await Promise.all([cargarTabla(), cargarTraspasos()]);
  } catch (error) {
    if (id !== cargaId || error.name === "AbortError") return;
    estadoCarga.textContent = "";
    document.getElementById("rango").textContent = "No se pudo actualizar el periodo";
    aviso.hidden = false;
    aviso.textContent = error.message + " Pulsa Actualizar para reintentar.";
  }
}

async function generarLectura() {
  if (generando || periodoVisible === null || !movimientosPeriodo) return;
  generando = true;
  const id = cargaId;
  const boton = document.getElementById("generar");
  const aviso = document.getElementById("aviso");
  boton.disabled = true;
  boton.textContent = "Generando…";
  aviso.hidden = true;
  const mes = periodoVisible;
  const nuevo = boton.dataset.nuevo === "1" ? "&nuevo=1" : "";
  try {
    const datos = await pedir("/api/lectura?mes=" + encodeURIComponent(mes) + nuevo, "POST");
    if (id === cargaId) pintarLectura(datos);
  } catch (error) {
    if (id === cargaId) {
      aviso.hidden = false;
      aviso.textContent = error.message;
    }
  } finally {
    generando = false;
    boton.disabled = periodoVisible === null || !movimientosPeriodo;
    boton.textContent = boton.dataset.nuevo === "1" ? "Volver a generar" : "Generar lectura";
  }
}

function limpiarFiltros() {
  for (const key of ["texto", "direccion", "origen", "categoria"]) tablaEstado[key] = "";
  tablaEstado.revisar = false;
  for (const id of ["buscar", "direccion", "origen", "filtro-categoria"]) document.getElementById(id).value = "";
  document.getElementById("solo-revisar").checked = false;
  pintarCategorias(categoriasCache, "");
}

async function exportarCsv() {
  const boton = document.getElementById("exportar");
  const id = tablaId;
  const periodo = periodoVisible;
  boton.disabled = true;
  try {
    const respuesta = await fetch("/api/movimientos.csv?" + parametrosTabla(), { cache: "no-store" });
    if (!respuesta.ok) throw new Error("No se pudo exportar el CSV. Inténtalo de nuevo.");
    const url = URL.createObjectURL(await respuesta.blob());
    const enlace = document.createElement("a");
    enlace.href = url;
    enlace.download = "movimientos-" + periodo + ".csv";
    document.body.append(enlace);
    enlace.click();
    enlace.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    if (id === tablaId) {
      const aviso = document.getElementById("aviso");
      aviso.hidden = false;
      aviso.textContent = error.message;
    }
  } finally {
    if (id === tablaId) boton.disabled = !tablaDatos || !tablaDatos.total;
  }
}

async function arrancar() {
  const select = document.getElementById("mes");
  opcion(select, "todos", "Todo el periodo");
  document.getElementById("actualizar").addEventListener("click", cargar);
  select.addEventListener("change", cargar);
  document.getElementById("vista-barras").addEventListener("click", () => {
    vista = "barras";
    document.getElementById("vista-barras").setAttribute("aria-pressed", "true");
    document.getElementById("vista-lineas").setAttribute("aria-pressed", "false");
    pintarEvolucion(serieCache, mesActivo);
  });
  document.getElementById("vista-lineas").addEventListener("click", () => {
    vista = "lineas";
    document.getElementById("vista-barras").setAttribute("aria-pressed", "false");
    document.getElementById("vista-lineas").setAttribute("aria-pressed", "true");
    pintarEvolucion(serieCache, mesActivo);
  });
  document.getElementById("series").addEventListener("click", (evento) => {
    const boton = evento.target.closest("[data-serie]");
    if (!boton) return;
    seriesOn[boton.dataset.serie] = !seriesOn[boton.dataset.serie];
    boton.setAttribute("aria-pressed", seriesOn[boton.dataset.serie] ? "true" : "false");
    pintarEvolucion(serieCache, mesActivo);
  });
  document.getElementById("evolucion").addEventListener("click", (evento) => {
    const hit = evento.target.closest("[data-mes]");
    if (hit) elegirMes(hit.getAttribute("data-mes"));
  });
  document.getElementById("evolucion").addEventListener("keydown", (evento) => {
    if (evento.key !== "Enter" && evento.key !== " ") return;
    const hit = evento.target.closest("[data-mes]");
    if (!hit) return;
    evento.preventDefault();
    elegirMes(hit.getAttribute("data-mes"));
  });
  document.getElementById("evolucion").addEventListener("mouseover", (evento) => {
    const hit = evento.target.closest("[data-mes]");
    if (!hit) return;
    const fila = serieCache.find((item) => item.mes === hit.getAttribute("data-mes"));
    if (fila) mostrarTip({ currentTarget: hit, clientX: evento.clientX, clientY: evento.clientY }, fila);
  });
  document.getElementById("evolucion").addEventListener("mouseleave", () => {
    document.getElementById("tip").hidden = true;
  });
  document.getElementById("evolucion").addEventListener("focusin", (evento) => {
    const hit = evento.target.closest("[data-mes]");
    if (!hit) return;
    const fila = serieCache.find((item) => item.mes === hit.getAttribute("data-mes"));
    if (fila) mostrarTip({ currentTarget: hit }, fila);
  });
  document.getElementById("evolucion").addEventListener("focusout", () => {
    document.getElementById("tip").hidden = true;
  });
  document.getElementById("categorias").addEventListener("click", (evento) => {
    const boton = evento.target.closest("[data-categoria]");
    if (boton) elegirCategoria(boton.dataset.categoria);
  });
  document.getElementById("generar").addEventListener("click", generarLectura);
  document.querySelector("thead").addEventListener("click", (evento) => {
    const boton = evento.target.closest("[data-sort]");
    if (boton) alOrdenar(boton.dataset.sort);
  });
  document.getElementById("buscar").addEventListener("input", (evento) => {
    tablaEstado.texto = evento.target.value;
    invalidarTabla();
    searchTimer = setTimeout(() => cargarTabla(), 200);
  });
  document.getElementById("direccion").addEventListener("change", (evento) => {
    tablaEstado.direccion = evento.target.value;
    cargarTabla();
  });
  document.getElementById("origen").addEventListener("change", (evento) => {
    tablaEstado.origen = evento.target.value;
    cargarTabla();
  });
  document.getElementById("filtro-categoria").addEventListener("change", (evento) => {
    tablaEstado.categoria = evento.target.value;
    pintarCategorias(categoriasCache, tablaEstado.categoria);
    cargarTabla();
  });
  document.getElementById("solo-revisar").addEventListener("change", (evento) => {
    tablaEstado.revisar = evento.target.checked;
    cargarTabla();
  });
  document.getElementById("limpiar-filtros").addEventListener("click", () => {
    limpiarFiltros();
    cargarTabla();
  });
  document.getElementById("ver-revision").addEventListener("click", () => {
    limpiarFiltros();
    tablaEstado.revisar = true;
    document.getElementById("solo-revisar").checked = true;
    cargarTabla();
    document.getElementById("movimientos").scrollIntoView({ behavior: "smooth" });
    document.getElementById("solo-revisar").focus({ preventScroll: true });
  });
  document.getElementById("tamano-pagina").addEventListener("change", (evento) => {
    tablaEstado.limit = Number(evento.target.value);
    cargarTabla();
  });
  document.getElementById("anterior").addEventListener("click", () => {
    tablaEstado.offset = Math.max(0, tablaEstado.offset - tablaEstado.limit);
    cargarTabla(false);
  });
  document.getElementById("siguiente").addEventListener("click", () => {
    tablaEstado.offset += tablaEstado.limit;
    cargarTabla(false);
  });
  document.getElementById("exportar").addEventListener("click", exportarCsv);
  const traspasos = document.getElementById("traspasos");
  traspasos.addEventListener("toggle", cargarTraspasos);
  document.addEventListener("cartera:sincronizada", cargarTraspasos);
  if (window.location?.hash === "#traspasos") traspasos.open = true;
  const dialog = document.getElementById("editar-categoria");
  const guardar = document.getElementById("editar-guardar");
  const cancelar = document.getElementById("editar-cancelar");
  cancelar.addEventListener("click", () => dialog.close());
  dialog.addEventListener("cancel", event => { if (guardar.disabled) event.preventDefault(); });
  document.getElementById("editar-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (!movimientoEditado || guardar.disabled) return;
    guardar.disabled = cancelar.disabled = true;
    try {
      await guardarCambio("/api/categoria-manual", { hash: movimientoEditado.hash, categoria: document.getElementById("editar-valor").value || null });
      dialog.close();
      await cargar();
    } catch (error) {
      document.getElementById("editar-error").textContent = error.message;
    } finally {
      guardar.disabled = cancelar.disabled = false;
    }
  });
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => pintarEvolucion(serieCache, mesActivo), 150);
  });
  opcion(document.getElementById("filtro-categoria"), "", "Todas");
  cargar();
}

arrancar();

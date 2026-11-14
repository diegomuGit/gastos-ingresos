(() => {
  const node = id => document.getElementById(id);
  const svgNS = 'http://www.w3.org/2000/svg';
  const eur = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' });
  const axis = new Intl.NumberFormat('es-ES', { notation: 'compact', maximumFractionDigits: 2 });
  const fmt = value => eur.format(Number(value));
  const signed = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR', signDisplay: 'exceptZero' });
  const day = iso => iso.split('-').reverse().join('/');
  const series = [
    { key: 'total', label: 'Patrimonio total', color: '#102b56' },
    { key: 'bbva', label: 'Saldo BBVA', color: '#005eae' },
    { key: 'myinvestor', label: 'MyInvestor', color: '#007668' },
    { key: 'cripto', label: 'Cripto', color: '#96520b' },
    { key: 'ajuste_traspasos', label: 'Ajuste por traspasos', color: '#6b4ba1' },
  ];
  const visible = new Set(series.map(s => s.key));
  const pageSize = 15;
  const host = node('patrimonio-grafico');
  const msDay = 86400000;
  let points = [];
  let index = 0;
  let page = 0;
  let selected = null;
  let controller;
  let updateFrame;

  function svg(tag, attributes, text) {
    const el = document.createElementNS(svgNS, tag);
    for (const [key, value] of Object.entries(attributes)) el.setAttribute(key, value);
    if (text !== undefined) el.textContent = text;
    return el;
  }

  function describe(index) {
    const point = points[index];
    if (!point) return;
    node('patrimonio-grafico-lectura').textContent = `${day(point.fecha)} · ` +
      series.map(s => `${s.label}: ${fmt(point[s.key])}`).join(' · ');
  }

  function consult(point) {
    document.dispatchEvent(new CustomEvent('patrimonio:consultar', { detail: point.fecha }));
  }

  function renderChart() {
    host.replaceChildren();
    if (!points.length) return;
    const active = series.filter(s => visible.has(s.key));
    const width = Math.max(260, Math.round(host.clientWidth || 640));
    const height = width < 480 ? 235 : 270;
    const left = 53, right = 16, top = 15, bottom = 32;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const first = points[0].time, last = points.at(-1).time;
    const span = last - first;
    const x = point => left + (span ? (point.time - first) / span : .5) * plotWidth;
    let min = 0, max = 0;
    for (const point of points) for (const s of active) {
      const value = Number(point[s.key]);
      min = Math.min(min, value);
      max = Math.max(max, value);
    }
    const padding = (max - min || 1) * .06;
    if (min < 0) min -= padding;
    max = Math.max(0, max + padding);
    const y = amount => top + (max - Number(amount)) / (max - min) * plotHeight;
    const graph = svg('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img',
      'aria-label': 'Evolución del patrimonio en euros. Los importes exactos están en la tabla por fecha.' });
    for (let i = 0; i <= 4; i++) {
      const value = min + (max - min) * i / 4;
      graph.append(svg('line', { x1: left, x2: width - right, y1: y(value), y2: y(value), stroke: '#d9e1e8' }),
        svg('text', { x: left - 5, y: y(value) + 4, 'text-anchor': 'end' }, `${axis.format(value)} €`));
    }
    const ticks = width < 520 ? [0, 1] : [0, 1/3, 2/3, 1];
    const times = new Set(ticks.map(tick => first + Math.round(tick * span / msDay) * msDay));
    for (const time of times) {
      const tick = span ? (time - first) / span : .5;
      graph.append(svg('text', { x: left + tick * plotWidth, y: height - 8, 'text-anchor': tick === 0 ? 'start' : tick === 1 ? 'end' : 'middle' },
        day(new Date(time).toISOString().slice(0, 10))));
    }
    for (const s of [...active].reverse()) {
      let continuous = '', gaps = '';
      for (let i = 1; i < points.length; i++) {
        const a = points[i - 1], b = points[i];
        const segment = `M${x(a)},${y(a[s.key])}L${x(b)},${y(b[s.key])}`;
        if (b.time - a.time > msDay) gaps += segment;
        else continuous += segment;
      }
      const stroke = { stroke: s.color, 'stroke-width': s.key === 'total' ? 3 : 1.8, fill: 'none' };
      if (continuous) graph.append(svg('path', { ...stroke, d: continuous }));
      if (gaps) graph.append(svg('path', { ...stroke, d: gaps, 'stroke-dasharray': '4 5', opacity: .7 }));
      if (points.length <= 120) for (const point of points) {
        graph.append(svg('circle', { cx: x(point), cy: y(point[s.key]), r: s.key === 'total' ? 2.7 : 2.1, fill: s.color }));
      }
    }
    const guide = svg('line', { y1: top, y2: height - bottom, stroke: '#6b7785', 'stroke-dasharray': '2 3' });
    const markers = active.map(s => svg('circle', { r: 4, fill: '#fff', stroke: s.color, 'stroke-width': 2 }));
    graph.append(guide, ...markers);
    const marker = next => {
      const point = points[next];
      const px = x(point);
      guide.setAttribute('x1', px);
      guide.setAttribute('x2', px);
      markers.forEach((circle, i) => {
        circle.setAttribute('cx', px);
        circle.setAttribute('cy', y(point[active[i].key]));
      });
      describe(next);
    };
    marker(index);
    const nearest = event => {
      const bounds = graph.getBoundingClientRect();
      const px = (event.clientX - bounds.left) * width / bounds.width;
      const time = first + (px - left) / plotWidth * span;
      let lo = 0, hi = points.length - 1;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (points[mid].time >= time) hi = mid;
        else lo = mid + 1;
      }
      return lo && time - points[lo - 1].time <= points[lo].time - time ? lo - 1 : lo;
    };
    graph.addEventListener('pointermove', event => marker(nearest(event)));
    graph.addEventListener('pointerleave', () => marker(index));
    graph.addEventListener('click', event => consult(points[nearest(event)]));
    host.append(graph);
  }

  function table() {
    const focused = node('patrimonio-cierres').contains(document.activeElement) ? document.activeElement.dataset.fecha : null;
    const start = Math.max(0, points.length - (page + 1) * pageSize);
    const end = points.length - page * pageSize;
    const rows = points.slice(start, end).reverse().map(point => {
      const row = document.createElement('tr');
      const date = document.createElement('td');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'chip';
      button.dataset.fecha = point.fecha;
      button.textContent = day(point.fecha);
      button.setAttribute('aria-label', `Consultar cierre del ${day(point.fecha)}`);
      if (point.fecha === selected) button.setAttribute('aria-current', 'date');
      button.addEventListener('click', () => consult(point));
      date.append(button);
      row.append(date);
      for (const s of series) {
        const cell = document.createElement('td');
        cell.textContent = fmt(point[s.key]);
        row.append(cell);
      }
      return row;
    });
    node('patrimonio-cierres').replaceChildren(...rows);
    if (focused) node('patrimonio-cierres').querySelector(`[data-fecha="${focused}"]`)?.focus({ preventScroll: true });
    node('patrimonio-pagina').textContent = `${points.length - end + 1}–${points.length - start} de ${points.length}`;
    node('patrimonio-mas-recientes').disabled = page === 0;
    node('patrimonio-mas-antiguos').disabled = start === 0;
  }

  function selectDate() {
    if (!points.length) return;
    const match = points.findIndex(p => p.fecha === selected);
    index = match === -1 ? points.length - 1 : match;
    if (match !== -1) page = Math.floor((points.length - 1 - index) / pageSize);
    renderChart();
    table();
  }

  function breakdown(data) {
    const host = node('patrimonio-desglose');
    const state = node('patrimonio-desglose-estado');
    host.replaceChildren();
    host.hidden = !data;
    if (!data) {
      state.textContent = 'Se necesitan dos cierres comunes para calcular la variación del periodo.';
      return;
    }
    const parts = [
      ['Variación total', data.variacion],
      ['Ahorro neto', data.ahorro],
      ['Rentabilidad de MyInvestor', data.myinvestor],
      [data.cripto_estimada ? 'Rentabilidad estimada de cripto' : 'Rentabilidad de cripto', data.cripto],
    ];
    for (const [name, value] of parts) {
      const item = document.createElement('div');
      const label = document.createElement('dt');
      const amount = document.createElement('dd');
      label.textContent = name;
      amount.textContent = value === null ? 'Sin datos' : signed.format(Number(value));
      item.append(label, amount);
      host.append(item);
    }
    if (data.exportacion_anterior) {
      state.textContent = 'Regenera la exportación de MyInvestor desde el README para obtener el desglose.';
    } else if (data.ahorro === null) {
      state.textContent = 'Faltan datos de flujos en el periodo para calcular todo el desglose.';
    } else if (data.cripto_estimada) {
      state.textContent = 'El ahorro neto es la variación total menos la rentabilidad. El desglose incluye flujos de cripto estimados.';
    } else {
      state.textContent = 'El ahorro neto es la variación total menos la rentabilidad de ambas carteras.';
    }
  }

  function renderWaterfall(data) {
    const chart = node('patrimonio-cascada-grafico');
    const state = node('patrimonio-cascada-estado');
    chart.replaceChildren();
    chart.hidden = true;
    state.hidden = false;
    if (!data) {
      state.textContent = 'Se necesitan dos cierres comunes para mostrar la cascada.';
      return;
    }
    if ([data.ahorro, data.myinvestor, data.cripto].some(value => value == null)) {
      state.textContent = 'La cascada estará disponible cuando se puedan calcular el ahorro y ambas rentabilidades.';
      return;
    }

    const initial = Number(points[0].total);
    const final = Number(points.at(-1).total);
    const contributions = [
      ['Ahorro neto', data.ahorro],
      ['Rentabilidad de MyInvestor', data.myinvestor],
      [data.cripto_estimada ? 'Rentabilidad estimada de cripto' : 'Rentabilidad de cripto', data.cripto],
    ];
    const steps = [{ name: 'Patrimonio inicial', date: data.desde, from: 0, to: initial, value: initial, total: true }];
    let balance = initial;
    for (const [name, amount] of contributions) {
      const value = Number(amount);
      steps.push({ name, from: balance, to: balance + value, value });
      balance += value;
    }
    steps.push({ name: 'Patrimonio final', date: data.hasta, from: 0, to: final, value: final, total: true });

    const levels = steps.map(step => step.to);
    const low = Math.min(...levels);
    const high = Math.max(...levels);
    const padding = Math.max((high - low) * .15, Math.abs(high) * .002, 1);
    const min = low < 0 ? low - padding : Math.max(0, Math.floor((low - padding) * 100) / 100);
    const max = Math.max(0, high) + padding;
    const position = value => (max - value) / (max - min) * 100;

    const layout = document.createElement('div');
    layout.className = 'patrimonio-cascada-layout';
    const scale = document.createElement('div');
    scale.className = 'patrimonio-cascada-eje';
    scale.setAttribute('aria-hidden', 'true');
    const columns = document.createElement('ol');
    columns.className = 'patrimonio-cascada-columnas';
    columns.setAttribute('role', 'list');
    for (let i = 0; i <= 4; i++) {
      const tick = document.createElement('span');
      tick.style.top = `${i * 25}%`;
      tick.textContent = `${axis.format(max - (max - min) * i / 4)} €`;
      scale.append(tick);
    }
    steps.forEach((step, i) => {
      const column = document.createElement('li');
      const plot = document.createElement('div');
      plot.className = 'patrimonio-cascada-columna';
      plot.setAttribute('aria-hidden', 'true');
      const bar = document.createElement('div');
      const base = step.total ? Math.max(0, min) : step.from;
      const top = position(Math.max(base, step.to));
      bar.className = 'patrimonio-cascada-barra';
      bar.dataset.tipo = step.total ? 'total' : step.value > 0 ? 'subida' : step.value < 0 ? 'bajada' : 'cero';
      bar.style.top = `${top}%`;
      bar.style.height = `${position(Math.min(base, step.to)) - top}%`;
      plot.append(bar);
      if (i < steps.length - 1) {
        const connector = document.createElement('span');
        connector.className = 'patrimonio-cascada-conector';
        connector.style.top = `${position(step.to)}%`;
        plot.append(connector);
      }
      const label = document.createElement('span');
      label.textContent = step.name;
      const amount = document.createElement('strong');
      amount.textContent = step.total ? fmt(step.value) : signed.format(step.value);
      column.append(plot, label, amount);
      if (step.date) {
        const date = document.createElement('time');
        date.dateTime = step.date;
        date.textContent = day(step.date);
        column.append(date);
      }
      columns.append(column);
    });
    layout.append(scale, columns);
    chart.append(layout);
    state.hidden = min <= 0;
    if (min > 0) state.textContent = `El eje vertical empieza en ${fmt(min)}.`;
    chart.hidden = false;
  }

  async function load() {
    controller?.abort();
    controller = new AbortController();
    const signal = controller.signal;
    const state = node('patrimonio-evolucion-estado');
    const content = node('patrimonio-evolucion-datos');
    state.textContent = 'Buscando cierres comunes…';
    content.hidden = true;
    try {
      const period = node('patrimonio-periodo').value;
      const res = await fetch('/api/patrimonio/evolucion?periodo=' + encodeURIComponent(period), { signal, cache: 'no-store' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'No se pudo consultar la evolución.');
      if (signal.aborted) return;
      points = data.puntos.map(p => ({ ...p, time: Date.parse(p.fecha) }));
      page = 0;
      const n = points.length;
      if (!n) {
        state.textContent = data.aviso || 'Todavía no hay cierres en los que se puedan comparar las tres fuentes y verificar los traspasos.';
        return;
      }
      state.textContent = (n === 1 ? `Un cierre común: ${day(points[0].fecha)}.`
        : `${n} cierres comunes · Del ${day(points[0].fecha)} al ${day(points.at(-1).fecha)}.`) +
        (data.omitidos ? ' Se han omitido fechas con valoraciones coincidentes pero sin saldo verificable o con traspasos pendientes de verificar.' : '');
      content.hidden = false;
      breakdown(data.desglose);
      renderWaterfall(data.desglose);
      selectDate();
    } catch (error) {
      if (!signal.aborted) state.textContent = error.message;
    }
  }

  for (const s of series) {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = true;
    const swatch = document.createElement('i');
    swatch.style.setProperty('--serie-color', s.color);
    swatch.setAttribute('aria-hidden', 'true');
    if (s.key === 'total') {
      label.className = 'patrimonio-serie-total';
      label.append(swatch, document.createTextNode(s.label));
      node('patrimonio-series').append(label);
      continue;
    }
    label.append(input, swatch, document.createTextNode(s.label));
    input.addEventListener('change', () => {
      if (input.checked) visible.add(s.key);
      else visible.delete(s.key);
      renderChart();
    });
    node('patrimonio-series').append(label);
  }
  node('patrimonio-mas-recientes').addEventListener('click', () => { page--; table(); });
  node('patrimonio-mas-antiguos').addEventListener('click', () => { page++; table(); });
  node('patrimonio-periodo').addEventListener('change', load);
  new ResizeObserver(() => {
    cancelAnimationFrame(updateFrame);
    updateFrame = requestAnimationFrame(() => {
      const width = Math.max(260, Math.round(host.clientWidth || 640));
      if (!node('patrimonio-evolucion-datos').hidden && host.firstChild?.viewBox.baseVal.width !== width) renderChart();
    });
  }).observe(host);
  document.addEventListener('patrimonio:cierre', event => {
    selected = event.detail;
    if (!node('patrimonio-evolucion-datos').hidden) selectDate();
  });
  document.addEventListener('finanzas:actualizado', load);
  document.addEventListener('cartera:sincronizada', load);
})();

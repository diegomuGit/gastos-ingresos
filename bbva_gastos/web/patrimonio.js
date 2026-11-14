(() => {
  const node = id => document.getElementById(id);
  const eur = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' });
  const fmt = value => value === null ? 'Sin dato' : eur.format(Number(value));
  const day = value => value ? value.split('-').reverse().join('/') : 'sin datos';
  let controller;
  let selected = '';

  function list(id, values) {
    node(id).replaceChildren(...values.map(text => {
      const item = document.createElement('li');
      item.textContent = text;
      return item;
    }));
  }

  function render(data) {
    const { fecha, ultimo, cuentas, traspasos, avisos, total } = data;
    node('patrimonio-dia').value = fecha || '';
    node('patrimonio-estado').textContent = fecha
      ? `Cierre del ${day(fecha)}${total === null ? ' · faltan datos para calcular el total' : selected ? '' : ' · última fecha común disponible'}.`
      : 'No hay fechas de valoración disponibles.';
    node('patrimonio-total').textContent = total === null ? 'Total no disponible' : fmt(total);
    const parts = [['Saldo BBVA', data.bbva], ['MyInvestor', data.myinvestor], ['Cripto', data.cripto]];
    if (traspasos.length) parts.push(['Ajuste por traspasos', data.ajuste_traspasos]);
    node('patrimonio-partes').replaceChildren(...parts.map(([label, value]) => {
      const item = document.createElement('div');
      const term = document.createElement('dt');
      const amount = document.createElement('dd');
      term.textContent = label;
      amount.textContent = fmt(value);
      item.append(term, amount);
      return item;
    }));
    const messages = [...avisos];
    if (!selected && total === null) messages.push(`Última fecha con datos: BBVA ${day(ultimo.bbva)} · MyInvestor ${day(ultimo.myinvestor)} · cripto ${day(ultimo.cripto)}.`);
    list('patrimonio-avisos', messages);
    node('patrimonio-avisos').hidden = !messages.length;
    const entries = cuentas.map((a, i) => `BBVA · ${cuentas.length > 1 ? `Cuenta ${i + 1}` : 'Cuenta'}: ${fmt(a.saldo)}${a.fecha_saldo ? ` · saldo del ${day(a.fecha_saldo)} (${a.fuente === 'manual' ? 'registrado manualmente' : 'extracto'})` : ''}${a.fecha_saldo && a.fecha_saldo !== fecha ? ' · sin movimientos hasta el cierre' : ''}.`);
    for (const t of traspasos) {
      entries.push(`${t.tipo === 'en_transito' ? 'Dinero en tránsito' : 'Abono ya reflejado en ambos saldos'}: ${fmt(t.importe)} · salida BBVA ${day(t.fecha_salida)} · abono MyInvestor ${day(t.fecha_abono)}.`);
    }
    list('patrimonio-apuntes', entries);
    node('patrimonio-detalle').hidden = !entries.length;
    node('patrimonio-datos').hidden = false;
    document.dispatchEvent(new CustomEvent('patrimonio:cierre', { detail: fecha }));
  }

  async function load() {
    controller?.abort();
    controller = new AbortController();
    const signal = controller.signal;
    node('patrimonio-datos').hidden = true;
    node('patrimonio-estado').textContent = 'Consultando el patrimonio…';
    try {
      const result = await fetch('/api/patrimonio' + (selected ? '?fecha=' + encodeURIComponent(selected) : ''), { signal, cache: 'no-store' });
      const data = await result.json();
      if (!result.ok) throw new Error(data.error || 'No se pudo consultar el patrimonio.');
      if (!signal.aborted) render(data);
    } catch (error) {
      if (!signal.aborted) node('patrimonio-estado').textContent = error.message;
    }
  }

  node('patrimonio-fecha').addEventListener('submit', event => {
    event.preventDefault();
    selected = node('patrimonio-dia').value;
    load();
  });
  node('patrimonio-ultima').addEventListener('click', () => { selected = ''; load(); });
  document.addEventListener('patrimonio:consultar', event => { selected = event.detail; load(); });
  document.addEventListener('finanzas:actualizado', load);
})();

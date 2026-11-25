// Utilidades compartidas por las páginas de Cartera inteligente: datos, formato y gráficos SVG.
(function (root) {
  "use strict";
  // Las APIs viven en analiza-dividendos (tiene acceso a los datos). ?api=<url> permite probar un preview.
  const params = new URLSearchParams(location.search);
  const API = (params.get("api") || "https://analiza-dividendos.vercel.app").replace(/\/+$/, "");

  async function api(path, init) {
    const res = await fetch(`${API}${path}`, init);
    let body = null;
    try { body = await res.json(); } catch { /* sin cuerpo */ }
    if (!res.ok) throw new Error(body?.error || `Error ${res.status}`);
    return body;
  }

  const nf = (d) => new Intl.NumberFormat("es-AR", { minimumFractionDigits: d, maximumFractionDigits: d });
  const fmt = (v, d = 0) => (v == null || !Number.isFinite(v) ? "s/d" : nf(d).format(v));
  const pct = (v, d = 1, sign = false) => (v == null || !Number.isFinite(v) ? "s/d" : `${sign && v > 0 ? "+" : ""}${nf(d).format(v * 100)}%`);
  const usd = (v, d = 0) => (v == null || !Number.isFinite(v) ? "s/d" : `US$ ${nf(d).format(v)}`);
  const ars = (v) => (v == null || !Number.isFinite(v) ? "s/d" : Math.abs(v) >= 1e6 ? `$ ${nf(2).format(v / 1e6)} M` : `$ ${nf(0).format(v)}`);
  const tone = (v) => (v == null ? "" : v < 0 ? "neg" : v > 0 ? "pos" : "");
  const date = (s) => (s ? new Date(`${String(s).slice(0, 10)}T12:00:00Z`).toLocaleDateString("es-AR", { day: "numeric", month: "short", year: "numeric" }) : "s/d");

  function el(tag, attrs, ...children) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) if (c != null) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
    return n;
  }

  function figure(label, value, note, cls) {
    return el("div", { class: "fig" }, el("span", { class: "fig-label", text: label }), el("span", { class: `fig-value ${cls || ""}`, text: value }), note ? el("span", { class: "fig-note", text: note }) : null);
  }

  function table(columns, rows, rowClass) {
    const thead = el("thead", {}, el("tr", {}, columns.map((c) => el("th", { class: c.num ? "num" : "", text: c.label }))));
    const tbody = el("tbody", {}, rows.map((r) => el("tr", { class: rowClass ? rowClass(r) : "" }, columns.map((c) => {
      const v = c.value(r);
      const cell = el("td", { class: [c.num ? "num" : "", c.tone ? tone(c.tone(r)) : ""].join(" ").trim() });
      if (v instanceof Node) cell.append(v); else cell.textContent = v;
      return cell;
    }))));
    return el("div", { class: "table-wrap" }, el("table", { class: "data" }, thead, tbody));
  }

  // Barras horizontales con cero al medio si hay negativos.
  function bars(items, { format = (v) => pct(v), max } = {}) {
    const m = max || Math.max(...items.map((i) => Math.abs(i.value)), 1e-9);
    const hasNeg = items.some((i) => i.value < 0);
    return el("div", { class: "bars" }, items.map((i) => {
      const w = (Math.abs(i.value) / m) * (hasNeg ? 50 : 100);
      const left = hasNeg ? (i.value < 0 ? 50 - w : 50) : 0;
      return el("div", { class: "bar-row" },
        el("span", { text: i.label }),
        el("span", { class: "bar-track" }, el("span", { class: `bar-fill ${i.value < 0 ? "neg" : ""}`, style: `left:${left}%;width:${w}%` })),
        el("span", { class: `bar-val ${tone(i.value)}`, text: format(i.value) }));
    }));
  }

  const SVG = "http://www.w3.org/2000/svg";
  function s(tag, attrs, text) {
    const n = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
    if (text != null) n.textContent = text;
    return n;
  }

  // Gráfico de líneas sobre fechas. series: [{ points: [{x: 'YYYY-MM-DD', y}], cls: 's1'|'s2' }], band opcional.
  function lineChart({ series, band, width = 760, height = 240, yFormat = (v) => fmt(v), title }) {
    const pad = { l: 56, r: 12, t: 12, b: 26 };
    const all = series.flatMap((x) => x.points).concat(band ? band.flatMap((b) => [{ x: b.x, y: b.lo }, { x: b.x, y: b.hi }]) : []);
    if (!all.length) return el("p", { class: "note", text: "Sin datos para graficar." });
    const t = (d) => Date.parse(`${d}T12:00:00Z`);
    const xs = all.map((p) => t(p.x));
    const ys = all.map((p) => p.y);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    let y0 = Math.min(...ys), y1 = Math.max(...ys);
    if (y0 === y1) { y0 -= 1; y1 += 1; }
    const span = y1 - y0; y0 -= span * 0.05; y1 += span * 0.05;
    const X = (d) => pad.l + ((t(d) - x0) / (x1 - x0 || 1)) * (width - pad.l - pad.r);
    const Y = (v) => pad.t + (1 - (v - y0) / (y1 - y0)) * (height - pad.t - pad.b);
    const svg = s("svg", { class: "chart", viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": title || "Gráfico" });
    for (let i = 0; i <= 4; i++) {
      const v = y0 + ((y1 - y0) * i) / 4;
      svg.append(s("line", { class: "grid", x1: pad.l, x2: width - pad.r, y1: Y(v), y2: Y(v) }));
      svg.append(s("text", { x: pad.l - 8, y: Y(v) + 4, "text-anchor": "end" }, yFormat(v)));
    }
    const years = new Set();
    const first = new Date(x0), last = new Date(x1);
    const spanDays = (x1 - x0) / 864e5;
    for (let d = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth(), 1)); d <= last; d.setUTCMonth(d.getUTCMonth() + 1)) {
      const step = spanDays > 1500 ? 12 : spanDays > 400 ? 6 : spanDays > 120 ? 2 : 1;
      if (d.getUTCMonth() % step) continue;
      const iso = d.toISOString().slice(0, 10);
      if (t(iso) < x0) continue;
      const label = spanDays > 1500 ? String(d.getUTCFullYear()) : d.toLocaleDateString("es-AR", { month: "short", year: "2-digit", timeZone: "UTC" });
      if (years.has(label)) continue;
      years.add(label);
      svg.append(s("text", { x: X(iso), y: height - 6, "text-anchor": "middle" }, label));
    }
    if (band && band.length) {
      const top = band.map((b) => `${X(b.x)},${Y(b.hi)}`);
      const bottom = [...band].reverse().map((b) => `${X(b.x)},${Y(b.lo)}`);
      svg.append(s("polygon", { class: "band", points: top.concat(bottom).join(" ") }));
    }
    for (const ser of series) {
      if (!ser.points.length) continue;
      svg.append(s("polyline", { class: ser.cls || "s1", points: ser.points.map((p) => `${X(p.x)},${Y(p.y)}`).join(" ") }));
    }
    return svg;
  }

  root.UI = { API, api, fmt, pct, usd, ars, tone, date, el, figure, table, bars, lineChart, svg: s };
})(window);

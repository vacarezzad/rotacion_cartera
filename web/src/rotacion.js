// Página Rotación: lee data/dashboard.json (lo genera scripts/export_web.py en el workflow semanal).
(function () {
  const { el, figure, table, fmt, pct, date, svg: s } = window.UI;
  const root = document.getElementById("root");

  // Tema claro/oscuro, mismo almacenamiento que el resto de la suite.
  const toggle = document.getElementById("themeToggle");
  toggle?.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("analiza-theme", next); } catch { /* sin storage */ }
  });

  const LIGHT = { "🟢": ["g", "Respaldo"], "🟡": ["y", "Mixto"], "🔴": ["r", "Alerta"] };
  const light = (e) => { const [c, t] = LIGHT[e] || ["", "s/d"]; return el("span", { class: `light ${c}`, text: t }); };
  const SERIES = [
    { key: "model5", color: "var(--accent)", main: true },
    { key: "model8", color: "var(--warn)" },
    { key: "ew30", color: "var(--ink-3)", dash: "5 4" },
    { key: "spy", color: "var(--muted-2)", dash: "2 3" },
  ];

  function block(id, title, lead, ...content) {
    return el("section", { class: "block", id }, el("header", {}, el("h2", { text: title })), lead ? el("p", { class: "lead", text: lead }) : null, ...content);
  }

  function spark(values) {
    if (!values?.length) return el("span", { class: "note", text: "s/d" });
    const w = 100, h = 30;
    const lo = Math.min(...values), hi = Math.max(...values);
    const pts = values.map((v, i) => `${(i / (values.length - 1)) * w},${h - ((v - lo) / (hi - lo || 1)) * h}`).join(" ");
    const g = s("svg", { class: `spark ${values.at(-1) >= values[0] ? "up" : "down"}`, viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "none", "aria-hidden": "true" });
    g.append(s("polyline", { points: pts }));
    return g;
  }

  function noteList(items, cls) {
    if (!items?.length) return null;
    return el("ul", { class: `notes ${cls || ""}` }, items.map((n) => el("li", { class: n.tone, text: n.text })));
  }

  const musd = (v) => (v == null ? "s/d" : v >= 1e6 ? `US$ ${fmt(v / 1e6, 1)} M` : `US$ ${fmt(v / 1e3, 0)} mil`);
  const plural = (n, a, b) => `${fmt(n)} ${n === 1 ? a : b}`;

  // Superinversores (Dataroma, 13F) e insiders, para una tarjeta de la cartera.
  function superBlock(r) {
    const d = r.dataroma;
    if (!d) return null;
    const lines = [];
    lines.push(el("p", { class: "sup-line" }, d.count
      ? `${plural(d.count, "superinversor lo tiene", "superinversores lo tienen")} · puesto ${fmt(d.rank)} · ${d.pct < 0.0001 ? "<0,01%" : pct(d.pct, 2)} de los portafolios`
      : "Ningún superinversor lo tiene"));
    if (d.holdPrice) lines.push(el("p", { class: "sup-line muted" }, `Hold price US$ ${fmt(d.holdPrice, 2)} (hoy US$ ${fmt(r.last, 2)})`));
    const act = el("p", { class: "sup-line" }, el("b", { text: `${d.quarter}: ` }),
      el("span", { class: "pos", text: `${d.buy} compran/suben` }), " · ", el("span", { class: "neg", text: `${d.sell} venden/bajan` }));
    lines.push(act);
    const who = [...d.buyers.map((b) => ({ ...b, tone: "pos" })), ...d.sellers.map((b) => ({ ...b, tone: "neg" }))];
    if (who.length) {
      lines.push(el("details", { class: "sup-who" }, el("summary", { text: "Quiénes" }),
        el("ul", { class: "notes compact" }, who.map((w) => el("li", { class: w.tone, text: `${w.manager.split(" - ")[0]}: ${w.action}` })))));
    }
    const ib = d.insiderBuys || {}, is = d.insiderSells || {};
    lines.push(el("p", { class: "sup-line muted" }, `Insiders 6 meses: ${plural(ib.n || 0, "compra", "compras")}${ib.n ? ` (${musd(ib.usd)})` : ""} · ${plural(is.n || 0, "venta", "ventas")}${is.n ? ` (${musd(is.usd)})` : ""}`));
    return el("div", { class: "sup" }, el("span", { class: "sup-title", text: "Superinversores · Dataroma" }), ...lines);
  }

  function superCell(r) {
    const d = r.dataroma;
    if (!d) return "s/d";
    return el("span", { class: "sup-cell" }, `${fmt(d.count)} `, el("span", { class: "pos", text: `+${d.buy}` }), " / ", el("span", { class: "neg", text: `−${d.sell}` }));
  }

  function action(r, sig) {
    if (sig.held.length === 0) return r.pick ? el("span", { class: "act buy", text: "Comprar" }) : null;
    if (r.pick && r.held) return el("span", { class: "act keep", text: "Mantener" });
    if (r.pick) return el("span", { class: "act buy", text: "Comprar" });
    if (r.held) return el("span", { class: "act sell", text: "Vender" });
    return null;
  }

  function daysTo(iso) {
    return Math.round((Date.parse(`${iso}T12:00:00Z`) - Date.now()) / 864e5);
  }

  function head(d) {
    const sig = d.signal;
    document.getElementById("pageTitle").textContent = `Cartera objetivo al ${date(sig.date)}`;
    const bt = d.backtest?.stats;
    const n = daysTo(sig.nextRebalance);
    return el("div", { class: "figures" },
      figure("Próximo rebalanceo", date(sig.nextRebalance), n > 0 ? `en ${n} días · ejecutar a la apertura siguiente` : "esta semana"),
      bt ? figure("Retorno anual modelo", pct(bt.model5.cagr, 1), `B&H 30: ${pct(bt.ew30.cagr, 1)} · SPY: ${pct(bt.spy.cagr, 1)}`, "up") : null,
      bt ? figure("Sharpe", fmt(bt.model5.sharpe, 2), `B&H 30: ${fmt(bt.ew30.sharpe, 2)}`) : null,
      bt ? figure("Peor caída", pct(bt.model5.maxdd, 0), `B&H 30: ${pct(bt.ew30.maxdd, 0)}`, "down") : null);
  }

  function cartera(d) {
    const sig = d.signal;
    const picks = sig.picks.map((t) => sig.rows.find((r) => r.ticker === t)).filter(Boolean);
    const sells = sig.rows.filter((r) => r.held && !r.pick);
    const cards = el("div", { class: "picks" }, picks.map((r) => el("article", { class: "pick" },
      el("div", { class: "pick-head" }, el("span", { class: "pick-tk", text: r.ticker }), el("span", { class: "pick-rank", text: `#${r.rank}` })),
      light(r.light),
      spark(r.spark),
      el("dl", {},
        el("dt", { text: "Score" }), el("dd", { text: fmt(r.score, 3) }),
        el("dt", { text: "Últimos 3 meses" }), el("dd", { class: r.ret3m < 0 ? "neg" : "pos", text: pct(r.ret3m, 0, true) }),
        el("dt", { text: "vs máximo 52 sem." }), el("dd", { text: pct(r.vsHigh, 0) }),
        el("dt", { text: "Sorpresa earnings" }), el("dd", { text: pct(r.surprise, 1, true) })),
      action(r, sig),
      noteList(r.notes),
      superBlock(r))));
    const what = sig.held.length
      ? `Cartera anotada: ${sig.held.join(", ")}. ${sig.buy.length ? `Comprar ${sig.buy.join(", ")}.` : ""} ${sig.sell.length ? `Vender ${sig.sell.join(", ")}.` : "No hay que vender nada."} Lo que ya tenés se mantiene mientras siga en el top ${sig.k + 2}.`
      : `Todavía no anotaste tu cartera en portfolio.json, así que la señal la arma desde cero.`;
    return block("cartera", "Cartera objetivo", `Top ${sig.k}, 20% cada uno. ${what}`, cards,
      sells.length ? el("p", { class: "note warn", text: `Salen de la cartera: ${sells.map((r) => `${r.ticker} (#${r.rank})`).join(", ")}.` }) : null,
      el("p", { class: "note", text: "Superinversores: carteras 13F que sigue Dataroma, con un trimestre de rezago. El semáforo es contexto fundamental (calidad contable SEC EDGAR, revisiones del EPS estimado, rentabilidad, precio objetivo y próximos balances). No cambia el ranking. El gráfico muestra el precio del último año en USD." }));
  }

  function ranking(d) {
    const sig = d.signal;
    const t = table([
      { label: "#", num: true, value: (r) => r.rank },
      { label: "Ticker", value: (r) => el("span", { class: "tk", text: r.ticker }) },
      { label: "Score", num: true, value: (r) => fmt(r.score, 3) },
      { label: "Modelo E4", num: true, value: (r) => fmt(r.e4, 2) },
      { label: "Sorpresa", num: true, value: (r) => pct(r.surprise, 1, true), tone: (r) => r.surprise },
      { label: "3 meses", num: true, value: (r) => pct(r.ret3m, 0, true), tone: (r) => r.ret3m },
      { label: "vs máx. 52s", num: true, value: (r) => pct(r.vsHigh, 0) },
      { label: "1 año", value: (r) => spark(r.spark) },
      { label: "Semáforo", value: (r) => light(r.light) },
      { label: "Superinv.", value: (r) => superCell(r) },
      { label: "Contexto", value: (r) => noteList(r.notes, "compact") || "" },
    ], sig.rows, (r) => [r.pick ? "is-pick" : "", r.rank === sig.k + 2 ? "cut" : ""].join(" "));
    t.querySelectorAll("tbody tr").forEach((tr) => tr.lastElementChild.classList.add("ctx"));
    return block("ranking", "Ranking completo", `Los ${sig.rows.length} CEDEARs ordenados por score = 75% modelo E4 + 25% sorpresa del último balance. La línea marca el top ${sig.k + 2}: lo que ya está en cartera se mantiene mientras no caiga debajo. Superinv.: cuántos superinversores de Dataroma lo tienen y cuántos compraron (+) o vendieron (−) en ${sig.dataromaQuarter || "el último trimestre"}.`, t);
  }

  function chart(bt) {
    const W = 760, H = 300, pad = { l: 52, r: 14, t: 12, b: 26 };
    const on = new Set(SERIES.map((x) => x.key));
    const t = bt.dates.map((x) => Date.parse(`${x}T12:00:00Z`));
    const box = el("div", { class: "chart-box" });
    const tip = el("div", { class: "tip", hidden: true });
    const buttons = el("div", { class: "series-toggle" }, SERIES.map((ser) => {
      const b = el("button", { type: "button", "aria-pressed": "true", style: `--c:${ser.color}` }, el("i", {}), bt.labels[ser.key]);
      b.addEventListener("click", () => {
        if (on.has(ser.key) && on.size > 1) on.delete(ser.key); else on.add(ser.key);
        b.setAttribute("aria-pressed", on.has(ser.key));
        draw();
      });
      return b;
    }));
    let svgEl;
    function draw() {
      const keys = SERIES.filter((x) => on.has(x.key));
      const vals = keys.flatMap((x) => bt.curves[x.key]).filter((v) => v > 0);
      const y0 = Math.log10(Math.min(...vals) * 0.9), y1 = Math.log10(Math.max(...vals) * 1.1);
      const X = (i) => pad.l + ((t[i] - t[0]) / (t.at(-1) - t[0])) * (W - pad.l - pad.r);
      const Y = (v) => pad.t + (1 - (Math.log10(v) - y0) / (y1 - y0)) * (H - pad.t - pad.b);
      const g = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Evolución de US$ 1 invertido, escala logarítmica" });
      const ticks = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000].filter((v) => Math.log10(v) >= y0 && Math.log10(v) <= y1);
      for (const v of ticks) {
        g.append(s("line", { class: "grid", x1: pad.l, x2: W - pad.r, y1: Y(v), y2: Y(v) }));
        g.append(s("text", { x: pad.l - 8, y: Y(v) + 4, "text-anchor": "end" }, `${fmt(v, v < 1 ? 1 : 0)}x`));
      }
      let lastYear = null;
      bt.dates.forEach((d, i) => {
        const y = +d.slice(0, 4);
        if (y !== lastYear && y % 2 === 0 && i > 0) g.append(s("text", { x: X(i), y: H - 6, "text-anchor": "middle" }, String(y)));
        lastYear = y;
      });
      for (const ser of keys.slice().reverse()) {
        const pts = bt.curves[ser.key].map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
        g.append(s("polyline", { class: `ln ${ser.main ? "main" : ""}`, points: pts, style: `stroke:${ser.color}`, ...(ser.dash ? { "stroke-dasharray": ser.dash } : {}) }));
      }
      const hl = s("line", { class: "hover-line", y1: pad.t, y2: H - pad.b, visibility: "hidden" });
      g.append(hl);
      g.addEventListener("pointermove", (ev) => {
        const r = g.getBoundingClientRect();
        const xv = ((ev.clientX - r.left) / r.width) * W;
        const tt = t[0] + ((xv - pad.l) / (W - pad.l - pad.r)) * (t.at(-1) - t[0]);
        let i = t.findIndex((v) => v >= tt); if (i < 0) i = t.length - 1; if (i < 0) i = 0;
        hl.setAttribute("x1", X(i)); hl.setAttribute("x2", X(i)); hl.setAttribute("visibility", "visible");
        tip.replaceChildren(el("b", { text: date(bt.dates[i]) }), ...keys.map((k) => el("span", {}, el("em", { text: bt.labels[k.key] }), `${fmt(bt.curves[k.key][i], 1)}x`)));
        tip.hidden = false;
        const px = (X(i) / W) * r.width;
        tip.style.left = `${Math.min(Math.max(px, 90), r.width - 90)}px`;
        tip.style.top = "8px";
      });
      g.addEventListener("pointerleave", () => { tip.hidden = true; hl.setAttribute("visibility", "hidden"); });
      if (svgEl) svgEl.replaceWith(g); else box.append(g);
      svgEl = g;
    }
    draw();
    box.append(tip);
    return [buttons, box];
  }

  function backtest(d) {
    const bt = d.backtest;
    if (!bt) return block("backtest", "Backtest", null, el("p", { class: "status", text: "Sin datos de backtest." }));
    const st = bt.stats;
    const rows = ["model5", "model8", "ew30", "spy"].map((k) => ({ k, ...st[k] }));
    const summary = table([
      { label: "Estrategia", value: (r) => bt.labels[r.k] },
      { label: "Retorno anual", num: true, value: (r) => pct(r.cagr, 1) },
      { label: "Volatilidad", num: true, value: (r) => pct(r.vol, 0) },
      { label: "Sharpe", num: true, value: (r) => fmt(r.sharpe, 2) },
      { label: "Peor caída", num: true, value: (r) => pct(r.maxdd, 0), tone: () => -1 },
      { label: "US$ 1 se volvió", num: true, value: (r) => `US$ ${fmt(1 + r.total, 1)}` },
    ], rows, (r) => (r.k === "model5" ? "is-pick" : ""));
    const wins = bt.years.filter((y) => y.model5 > y.ew30).length;
    const years = el("div", { class: "years" }, bt.years.map((y) => {
      const ex = y.model5 - y.ew30;
      return el("div", { class: "yr" }, el("b", { text: String(y.year) }), el("span", { class: `ex ${ex < 0 ? "neg" : "pos"}`, text: pct(ex, 0, true) }), el("small", { text: `${pct(y.model5, 0)} vs ${pct(y.ew30, 0)}` }));
    }));
    return block("backtest", "Backtest contra buy & hold",
      `Walk-forward ${date(bt.from)} a ${date(bt.to)}: el modelo se reentrena cada trimestre solo con datos pasados. Neto de comisiones, derechos y spread de IOL. Escala logarítmica.`,
      ...chart(bt), summary,
      el("h3", { class: "sub", text: `Diferencia anual contra el B&H equiponderado (le gana ${wins} de ${bt.years.length} años)` }), years,
      el("p", { class: "note warn", text: "Universo elegido con los líquidos de hoy (sesgo de supervivencia), ~90 variantes probadas y volatilidad ~34% anual. Caídas de 35–40% son esperables. Recomendado: un trimestre de paper trading antes de operar." }));
  }

  function seguimiento(d) {
    const h = (d.history || []).slice().reverse();
    const t = table([
      { label: "Señal", value: (r) => date(r.date) },
      { label: "Cartera objetivo", value: (r) => r.picks.join(" · ") },
      { label: "Desde entonces", num: true, value: (r) => pct(r.portRet, 1, true), tone: (r) => r.portRet },
      { label: "SPY", num: true, value: (r) => pct(r.spyRet, 1, true), tone: (r) => r.spyRet },
    ], h);
    return block("seguimiento", "Seguimiento en vivo", "Cada señal publicada queda guardada. Acá se ve cómo le fue a su cartera objetivo (equiponderada, en USD, sin costos) desde ese día, contra SPY. Es el control honesto fuera del backtest.", t,
      h.every((r) => r.portRet == null) ? el("p", { class: "note", text: "La primera señal es de esta semana: el seguimiento se completa desde el próximo viernes." }) : null);
  }

  // ABM de operaciones: /api/operaciones lee y escribe operaciones.csv en GitHub.
  function abm(d, onSaved, hooks = {}) {
    const API = "api/operaciones";
    const tickers = (d.signal?.rows || []).map((r) => r.ticker).sort();
    const today = new Date().toISOString().slice(0, 10);
    let state = { sha: null, rows: [] }, editing = null, confirmDel = null;
    const msg = el("p", { class: "status abm-msg", role: "status" });
    const say = (t, cls) => { msg.textContent = t || ""; msg.className = `status abm-msg ${cls || ""}`; };
    const field = (label, input) => el("label", {}, label, input);
    const inp = (name, attrs) => el("input", { name, ...attrs });
    const f = {
      fecha: inp("fecha", { type: "date", required: true, value: today }),
      ticker: inp("ticker", { list: "abm-tickers", required: true, placeholder: "NVDA", autocomplete: "off", style: "width:96px;text-transform:uppercase" }),
      operacion: el("select", { name: "operacion" }, el("option", { value: "compra", text: "Compra" }), el("option", { value: "venta", text: "Venta" })),
      cantidad: inp("cantidad", { type: "number", min: "1", step: "1", required: true, placeholder: "CEDEARs" }),
      total_ars: inp("total_ars", { type: "number", min: "0", step: "0.01", required: true, placeholder: "con comisión", style: "width:150px" }),
      nota: inp("nota", { type: "text", maxlength: "200", placeholder: "opcional", style: "width:180px" }),
      ccl: inp("ccl", { type: "number", min: "0", step: "0.01", placeholder: "automático" }),
      ratio: inp("ratio", { type: "number", min: "0", step: "any", placeholder: "automático" }),
    };
    const submit = el("button", { type: "submit", class: "btn", text: "Agregar" });
    const cancel = el("button", { type: "button", class: "btn ghost", text: "Cancelar", hidden: true, onclick: () => reset() });
    const adv = el("details", { class: "abm-adv" }, el("summary", { text: "CCL y ratio (se completan solos)" }),
      el("div", { class: "abm-adv-body" }, field("CCL", f.ccl), field("Ratio", f.ratio)));
    const form = el("form", { class: "inline abm-form" },
      field("Fecha", f.fecha), field("Ticker", f.ticker), field("Operación", f.operacion), field("Cantidad", f.cantidad),
      field("Total ARS (comisión incluida)", f.total_ars), field("Nota", f.nota), submit, cancel, adv,
      el("datalist", { id: "abm-tickers" }, tickers.map((t) => el("option", { value: t }))));
    const list = el("div", {});

    function reset() {
      editing = null; form.reset(); f.fecha.value = today; f.cantidad.removeAttribute("max"); submit.textContent = "Agregar"; cancel.hidden = true; adv.open = false;
    }
    // Total con comisión: compra = lo que pagaste; venta = lo que cobraste neto.
    const totalOf = (r) => { const t = (+r.cantidad || 0) * (+r.precio_ars || 0), c = +r.comision_ars || 0; return r.operacion === "venta" ? t - c : t + c; };
    function load(r) {
      for (const k of Object.keys(f)) f[k].value = r[k] ?? "";
      f.total_ars.value = r.cantidad && r.precio_ars ? +totalOf(r).toFixed(2) : "";
      if (r.ccl || r.ratio) adv.open = true;
    }
    hooks.removeTicker = (t) => save({ action: "deleteTicker", row: { ticker: t } }, `Borrada la posición de ${t} con todas sus operaciones.`);
    hooks.sell = (p) => {
      reset(); editing = null;
      f.operacion.value = "venta"; f.ticker.value = p.ticker; f.cantidad.value = p.qty; f.cantidad.max = p.qty;
      submit.textContent = "Registrar venta"; cancel.hidden = false;
      say(`Venta de ${p.ticker}: tenés ${fmt(p.qty)} CEDEARs. Poné lo que cobraste neto de comisión.`);
      form.scrollIntoView({ behavior: "smooth", block: "center" }); f.total_ars.focus();
    };
    async function call(body) {
      const r = await fetch(API, { method: body ? "POST" : "GET", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
      const j = await r.json().catch(() => ({ error: `Error ${r.status}` }));
      if (j.rows) state = { sha: j.sha, rows: j.rows };
      if (!r.ok) throw new Error(j.error || `Error ${r.status}`);
      return j;
    }
    async function save(body, okText) {
      form.querySelectorAll("button").forEach((b) => (b.disabled = true));
      say("Guardando…");
      try {
        await call({ ...body, sha: state.sha });
        reset(); say(`${okText} Costo, valor y resultado en USD se recalculan en 1 o 2 minutos.`, "ok"); onSaved?.();
      } catch (e) { say(e.message, "err"); }
      form.querySelectorAll("button").forEach((b) => (b.disabled = false));
      render();
    }
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const row = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.value.trim()]));
      row.ticker = row.ticker.toUpperCase();
      row.precio_ars = String(+(+row.total_ars / +row.cantidad).toFixed(4)); row.comision_ars = ""; delete row.total_ars;
      if (editing != null) save({ action: "update", index: editing, row }, `Modificada la operación de ${row.ticker}.`);
      else save({ action: "add", row }, `Agregada: ${row.operacion} de ${row.cantidad} ${row.ticker}.`);
    });

    function render() {
      const rows = state.rows.map((r, i) => ({ ...r, i })).reverse();
      if (!rows.length) { list.replaceChildren(el("p", { class: "note", text: "Todavía no hay operaciones cargadas." })); return; }
      list.replaceChildren(table([
        { label: "Fecha", value: (r) => date(r.fecha) },
        { label: "Ticker", value: (r) => el("span", { class: "tk", text: r.ticker }) },
        { label: "Operación", value: (r) => el("span", { class: `act ${r.operacion === "compra" ? "buy" : "sell"}`, text: r.operacion === "compra" ? "Compra" : "Venta" }) },
        { label: "Cantidad", num: true, value: (r) => fmt(+r.cantidad) },
        { label: "Total ARS", num: true, value: (r) => fmt(totalOf(r), 2) },
        { label: "Precio ARS", num: true, value: (r) => fmt(totalOf(r) / +r.cantidad, 2) },
        { label: "Nota", value: (r) => r.nota || "" },
        { label: "", value: (r) => el("span", { class: "abm-actions" },
            el("button", { type: "button", class: "btn ghost sm", text: "Editar", onclick: () => { editing = r.i; confirmDel = null; load(r); submit.textContent = "Guardar cambios"; cancel.hidden = false; say(`Editando ${r.ticker} del ${date(r.fecha)}.`); form.scrollIntoView({ behavior: "smooth", block: "center" }); render(); } }),
            confirmDel === r.i
              ? el("button", { type: "button", class: "btn danger sm", text: "¿Borrar?", onclick: () => { confirmDel = null; save({ action: "delete", index: r.i }, `Borrada la operación de ${r.ticker}.`); } })
              : el("button", { type: "button", class: "btn ghost sm", text: "Borrar", onclick: () => { confirmDel = r.i; render(); } })) },
      ], rows, (r) => (r.i === editing ? "is-pick" : "")));
    }

    const box = el("div", { class: "abm" }, el("h3", { class: "sub", text: "Cargar operación" }), form, msg,
      el("h3", { class: "sub", text: "Operaciones" }), list);
    list.replaceChildren(el("p", { class: "note", text: "Cargando operaciones…" }));
    call().then(render).catch((e) => {
      form.querySelectorAll("input,select,button").forEach((x) => (x.disabled = true));
      say(`No se puede editar desde acá: ${e.message}.`, "err");
      state.rows = (d.mine?.trades || []).map((t) => ({ fecha: t.date, ticker: t.ticker, operacion: t.op, cantidad: t.qty, precio_ars: t.priceArs, nota: t.note })).reverse();
      render(); list.querySelectorAll("button").forEach((x) => (x.disabled = true));
    });
    return box;
  }

  function mine(d) {
    const m = d.mine || { trades: [], positions: [] };
    const P = m.positions || [];
    const parts = [];
    const hooks = {};
    if (P.length || (m.sales || []).length) {
      const cost = P.reduce((a, p) => a + (p.costUsd || 0), 0);
      const val = P.reduce((a, p) => a + (p.valueUsd || 0), 0);
      parts.push(el("div", { class: "figures" },
        figure("Posiciones abiertas", fmt(P.length), P.map((p) => p.ticker).join(" · ")),
        figure("Invertido", `US$ ${fmt(cost, 0)}`, "pesos pagados ÷ CCL de cada compra"),
        figure("Valor hoy", val ? `US$ ${fmt(val, 0)}` : "s/d", "precio del subyacente × acciones equivalentes"),
        figure("Resultado", val && cost ? pct(val / cost - 1, 1, true) : "s/d", val && cost ? `US$ ${fmt(val - cost, 0)}` : "", val >= cost ? "up" : "down"),
        figure("Ganancia realizada", `US$ ${fmt(m.realizedUsd || 0, 0)}`, "lo ya vendido, neto de comisiones", (m.realizedUsd || 0) >= 0 ? "up" : "down")));
      if (P.length) parts.push(table([
        { label: "Ticker", value: (r) => el("span", { class: "tk", text: r.ticker }) },
        { label: "Desde", value: (r) => date(r.since) },
        { label: "CEDEARs", num: true, value: (r) => fmt(r.qty) },
        { label: "Costo ARS", num: true, value: (r) => `$ ${fmt(r.costArs, 0)}` },
        { label: "Costo USD", num: true, value: (r) => (r.costUsd == null ? "s/d" : fmt(r.costUsd, 0)) },
        { label: "Valor USD", num: true, value: (r) => (r.valueUsd == null ? "s/d" : fmt(r.valueUsd, 0)) },
        { label: "Resultado USD", num: true, value: (r) => (r.pnlUsd == null ? "s/d" : fmt(r.pnlUsd, 0)), tone: (r) => r.pnlUsd },
        { label: "Resultado", num: true, value: (r) => pct(r.pnlPct, 1, true), tone: (r) => r.pnlPct },
        { label: "", value: (r) => el("span", { class: "abm-actions" },
            el("button", { type: "button", class: "btn ghost sm", text: "Vender", onclick: () => hooks.sell?.(r) }),
            el("button", { type: "button", class: "btn ghost sm", text: "Borrar", title: "Borra todas las operaciones de este ticker (para corregir una carga)",
              onclick: (e) => {
                const b = e.currentTarget;
                if (b.dataset.confirm) return hooks.removeTicker?.(r.ticker);
                b.dataset.confirm = "1"; b.textContent = "¿Borrar todo?"; b.className = "btn danger sm";
                setTimeout(() => { delete b.dataset.confirm; b.textContent = "Borrar"; b.className = "btn ghost sm"; }, 4000);
              } })) },
      ], P));
    }
    const S = m.sales || [];
    if (S.length) {
      parts.push(el("h3", { class: "sub", text: "Ventas y ganancia real" }), table([
        { label: "Venta", value: (r) => date(r.date) },
        { label: "Ticker", value: (r) => el("span", { class: "tk", text: r.ticker }) },
        { label: "Comprado", value: (r) => date(r.since) },
        { label: "CEDEARs", num: true, value: (r) => fmt(r.qty) },
        { label: "Costo USD", num: true, value: (r) => (r.costUsd == null ? "s/d" : fmt(r.costUsd, 2)) },
        { label: "Cobrado USD", num: true, value: (r) => (r.proceedsUsd == null ? "s/d" : fmt(r.proceedsUsd, 2)) },
        { label: "Ganancia USD", num: true, value: (r) => (r.pnlUsd == null ? "s/d" : fmt(r.pnlUsd, 2)), tone: (r) => r.pnlUsd },
        { label: "Ganancia", num: true, value: (r) => pct(r.pnlPct, 1, true), tone: (r) => r.pnlPct },
        { label: "Ganancia ARS", num: true, value: (r) => fmt(r.pnlArs, 0), tone: (r) => r.pnlArs },
      ], S), el("p", { class: "note", text: "Ganancia real: lo cobrado neto de comisiones menos lo pagado con comisiones (costo promedio), cada uno pasado a dólares con el CCL del día de la operación." }));
    }
    parts.push(abm(d, null, hooks));
    return block("mia", "Mi cartera real", "Cargá cada compra o venta en pesos, con la comisión incluida; el ratio y el CCL del día salen de Supabase, así que costo, valor y ganancia se ven en dólares. La señal usa estas posiciones para el buffer del top 7.", ...parts);
  }

  function metodo() {
    const items = [
      ["Qué predice", "El retorno de la acción subyacente (en USD) a 63 días hábiles, relativo a la mediana de los 30. Se analiza el subyacente, no el CEDEAR."],
      ["Modelo E4", "Ensamble de LightGBM y Ridge con momentum, volatilidad, distancia a medias, RSI, fuerza relativa contra SPY y régimen de mercado (VIX, tasa a 10 años, amplitud)."],
      ["Sorpresa de earnings", "Cuánto superó o no el último balance a lo esperado. Pesa 25% del score; cuenta desde el día hábil siguiente al reporte."],
      ["Rebalanceo", "Último día hábil de enero, abril, julio y octubre, con la señal al cierre y ejecución a la apertura siguiente. Lo que ya tenés se mantiene si sigue en el top 7, para ahorrar costos."],
      ["Semáforo", "Verde, amarillo o rojo según calidad contable (SEC EDGAR: accruals, deuda, flujo de caja, márgenes), revisiones del EPS estimado, rentabilidad y precio objetivo. Es contexto, no entra en el score."],
      ["Qué no funcionó", "Swing con take profit y stop loss, rotación de ETFs, timing con VIX, RSI(2) y estacionalidad, vol-targeting y core-satellite. Ninguno le ganó al buy & hold neto de costos."],
    ];
    const half = Math.ceil(items.length / 2);
    const dl = (arr) => el("dl", {}, arr.flatMap(([a, b]) => [el("dt", { text: a }), el("dd", { text: b })]));
    const body = el("div", { class: "method", id: "metodo-body", hidden: true }, dl(items.slice(0, half)), dl(items.slice(half)));
    const btn = el("button", { type: "button", class: "btn ghost", "aria-expanded": "false", "aria-controls": "metodo-body", text: "Ver cómo funciona",
      onclick: () => { const open = body.hidden; body.hidden = !open; btn.setAttribute("aria-expanded", String(open)); btn.textContent = open ? "Ocultar" : "Ver cómo funciona"; } });
    // Si se entra por el link #metodo del menú, se abre.
    const openIfHash = () => { if (location.hash === "#metodo" && body.hidden) btn.click(); };
    window.addEventListener("hashchange", openIfHash); setTimeout(openIfHash, 0);
    return block("metodo", "Cómo funciona", null, btn, body);
  }

  // Resalta en el subnav la sección visible.
  function spy() {
    const links = [...document.querySelectorAll(".subnav a")];
    const io = new IntersectionObserver((es) => {
      for (const e of es) if (e.isIntersecting) links.forEach((a) => a.setAttribute("aria-current", a.hash === `#${e.target.id}` ? "page" : "false"));
    }, { rootMargin: "-40% 0px -55% 0px" });
    document.querySelectorAll(".block[id]").forEach((b) => io.observe(b));
  }

  fetch(`data/dashboard.json?t=${Date.now()}`)
    .then((r) => { if (!r.ok) throw new Error(`Error ${r.status}`); return r.json(); })
    .then((d) => {
      if (!d.signal) throw new Error("Todavía no hay señales publicadas.");
      root.replaceChildren(head(d), cartera(d), ranking(d), mine(d), seguimiento(d), backtest(d), metodo(),
        el("p", { class: "note", text: `Datos actualizados ${d.generated.replace("T", " ").replace("Z", " UTC")}. Se regeneran cada viernes a las 19:30 (hora argentina).` }));
      spy();
    })
    .catch((e) => root.replaceChildren(el("p", { class: "err", text: `No se pudieron cargar los datos: ${e.message}` })));
})();

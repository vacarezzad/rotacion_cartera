// ABM de operaciones: lee y escribe operaciones.csv en GitHub (el CSV sigue siendo la fuente de verdad).
// Cada cambio es un commit; el workflow operaciones.yml recalcula la web en 1-2 minutos.
// Variables en Vercel: GITHUB_TOKEN (token fine-grained con Contents: read/write solo sobre este repo).
// La página y esta API quedan detrás de la protección de Vercel (solo vos las ves).
const REPO = process.env.GITHUB_REPO || "vacarezzad/rotacion_cartera";
const BRANCH = process.env.GITHUB_BRANCH || "main";
const PATH = "operaciones.csv";
const COLS = ["fecha", "ticker", "operacion", "cantidad", "precio_ars", "ccl", "ratio", "comision_ars", "nota"];
const API = `https://api.github.com/repos/${REPO}/contents/${PATH}`;

function parseLine(line) {
  const out = []; let cur = "", q = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (q) { if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (c === '"') q = false; else cur += c; }
    else if (c === '"') q = true; else if (c === ",") { out.push(cur); cur = ""; } else cur += c;
  }
  out.push(cur);
  return out;
}
const cell = (v) => { v = v == null ? "" : String(v); return /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v; };
const parse = (txt) => txt.split(/\r?\n/).slice(1).filter((l) => l.trim()).map((l) => {
  const v = parseLine(l); return Object.fromEntries(COLS.map((c, i) => [c, (v[i] ?? "").trim()]));
});
const toCsv = (rows) => [COLS.join(","), ...rows.map((r) => COLS.map((c) => cell(r[c])).join(","))].join("\n") + "\n";

function clean(r) {
  const num = (v, req) => {
    const s = String(v ?? "").trim().replace(",", ".");
    if (!s) { if (req) throw new Error("Faltan cantidad o precio"); return ""; }
    const n = Number(s); if (!Number.isFinite(n) || n < 0) throw new Error(`Número inválido: ${v}`); return String(n);
  };
  const fecha = String(r.fecha || "").trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(fecha)) throw new Error("Fecha inválida (AAAA-MM-DD)");
  const ticker = String(r.ticker || "").trim().toUpperCase();
  if (!/^[A-Z0-9.]{1,10}$/.test(ticker)) throw new Error("Ticker inválido");
  const op = String(r.operacion || "").trim().toLowerCase();
  if (!["compra", "venta"].includes(op)) throw new Error("La operación tiene que ser compra o venta");
  const cantidad = num(r.cantidad, true);
  if (Number(cantidad) <= 0) throw new Error("La cantidad tiene que ser mayor a cero");
  return { fecha, ticker, operacion: op, cantidad, precio_ars: num(r.precio_ars, true), ccl: num(r.ccl), ratio: num(r.ratio),
           comision_ars: num(r.comision_ars), nota: String(r.nota || "").replace(/[\r\n]+/g, " ").trim().slice(0, 200) };
}

async function gh(method, body) {
  const r = await fetch(method === "GET" ? `${API}?ref=${BRANCH}` : API, {
    method, headers: { Authorization: `Bearer ${process.env.GITHUB_TOKEN}`, Accept: "application/vnd.github+json",
                       "User-Agent": "rotacion-abm", "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(j.message || `GitHub ${r.status}`); e.status = r.status; throw e; }
  return j;
}

async function read() {
  const f = await gh("GET");
  return { sha: f.sha, rows: parse(Buffer.from(f.content, "base64").toString("utf8")) };
}

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  try {
    if (!process.env.GITHUB_TOKEN) return res.status(503).json({ error: "Falta configurar GITHUB_TOKEN en Vercel" });
    if (req.method === "GET") return res.status(200).json(await read());
    if (req.method !== "POST") return res.status(405).json({ error: "Método no permitido" });
    const { action, index, row, sha } = typeof req.body === "string" ? JSON.parse(req.body) : req.body || {};
    const cur = await read();
    if (sha && sha !== cur.sha) return res.status(409).json({ error: "El archivo cambió mientras editabas; recargá la lista", ...cur });
    const rows = cur.rows.slice();
    let msg;
    if (action === "add") { const r = clean(row); rows.push(r); msg = `operaciones: alta ${r.operacion} ${r.cantidad} ${r.ticker} (${r.fecha})`; }
    else if (action === "update" || action === "delete") {
      if (!Number.isInteger(index) || index < 0 || index >= rows.length) return res.status(400).json({ error: "Fila inexistente" });
      if (action === "update") { rows[index] = clean(row); msg = `operaciones: modifica ${rows[index].ticker} (${rows[index].fecha})`; }
      else { const [r] = rows.splice(index, 1); msg = `operaciones: baja ${r.operacion} ${r.cantidad} ${r.ticker} (${r.fecha})`; }
    } else return res.status(400).json({ error: "Acción inválida" });
    rows.sort((a, b) => a.fecha.localeCompare(b.fecha));
    const put = await gh("PUT", { message: msg, content: Buffer.from(toCsv(rows)).toString("base64"), sha: cur.sha, branch: BRANCH });
    return res.status(200).json({ sha: put.content.sha, rows });
  } catch (e) {
    return res.status(e.status === 409 ? 409 : e.status ? 502 : 400).json({ error: e.message });
  }
};

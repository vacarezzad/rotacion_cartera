"""Parsea el HTML de Dataroma (data/dataroma/raw) a data/dataroma/dataroma.json.

Por ticker: estadísticas de superinversores (cantidad, ranking, % de todos los portafolios, hold price),
actividad del último trimestre reportado (compras/aumentos vs ventas/reducciones, con nombres) e
insiders de los últimos 6 meses (compras y ventas, cantidad y monto).
"""
import html
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "dataroma", "raw")
OUT = os.path.join(ROOT, "data", "dataroma", "dataroma.json")


def txt(x):
    return html.unescape(re.sub(r"<[^>]+>", "", x)).replace("\xa0", " ").strip()


def num(x):
    x = re.sub(r"[^0-9.\-]", "", x or "")
    try:
        return float(x)
    except ValueError:
        return None


def stats(s):
    out = {}
    for key, label in [("count", "Ownership count:"), ("rank", "Ownership rank:"), ("pct", "% of all portfolios:")]:
        m = re.search(re.escape(label) + r"</td>\s*<td>(.*?)</td>", s, re.S)
        v = num(txt(m.group(1))) if m else None
        out[key] = v / 100 if key == "pct" and v is not None else v
    m = re.search(r'<td id="price">(.*?)</td>', s, re.S)
    out["holdPrice"] = num(txt(m.group(1))) if m else None
    for side in ["buys", "sells"]:
        m = re.search(rf'<tr class="{side}">\s*<td>.*?</td>\s*<td class="num">(.*?)</td>\s*<td class="num">(.*?)</td>', s, re.S)
        out[f"insider_{side}"] = {"n": int(num(txt(m.group(1))) or 0), "usd": num(txt(m.group(2)))} if m else {"n": 0, "usd": 0}
    return out


def activity(s):
    """Último trimestre de la página de actividad."""
    parts = re.split(r'<tr class="q_chg">', s)
    if len(parts) < 2:
        return None
    blk = parts[1]
    q = re.search(r"<b>(Q\d)</b>.*?<b>(\d{4})</b>", blk, re.S)
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", blk, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if len(tds) < 4:
            continue
        firm, act = txt(tds[1]), txt(tds[2])
        if not firm or not act:
            continue
        kind = act.split()[0].lower()
        rows.append({"manager": firm, "action": act, "kind": kind})
    buy = [r for r in rows if r["kind"] in ("buy", "add")]
    sell = [r for r in rows if r["kind"] in ("sell", "reduce")]
    return {"quarter": f"{q.group(1)} {q.group(2)}" if q else None,
            "buys": len([r for r in rows if r["kind"] == "buy"]), "adds": len([r for r in rows if r["kind"] == "add"]),
            "reduces": len([r for r in rows if r["kind"] == "reduce"]), "sells": len([r for r in rows if r["kind"] == "sell"]),
            "buyers": buy, "sellers": sell}


def main():
    data = {}
    for f in sorted(os.listdir(RAW)):
        if not f.endswith(".html") or f.endswith("_act.html"):
            continue
        t = f[:-5]
        s = open(os.path.join(RAW, f)).read()
        if "Ownership count" not in s:
            continue
        d = stats(s)
        fa = os.path.join(RAW, f"{t}_act.html")
        d["activity"] = activity(open(fa).read()) if os.path.exists(fa) else None
        data[t] = d
    json.dump(data, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"dataroma.json: {len(data)} tickers")


if __name__ == "__main__":
    main()

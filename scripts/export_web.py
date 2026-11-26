"""Exporta los datos que lee la web (web/data/*.json) a partir de signals/, results/ y data/prices/.

No entrena nada: solo junta lo que ya produjo make_signal.py y los experimentos.
Uso: python scripts/export_web.py
"""
import glob
import json
import math
import os

import numpy as np
import pandas as pd
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tml.positions import _ratio_on, _ratios, held_tickers, load_trades, positions  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "data")
CURVES = os.path.join(ROOT, "results", "results_30f", "u30f_blend_curvas.csv")
K = 5


def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not math.isfinite(float(o)) else round(float(o), 5)
    if isinstance(o, np.integer):
        return int(o)
    return o


def close(t):
    fp = os.path.join(ROOT, "data", "prices", f"{t}.csv")
    if not os.path.exists(fp):
        return None
    d = pd.read_csv(fp, parse_dates=["Date"]).set_index("Date")["Close"].dropna()
    return d[~d.index.duplicated(keep="last")].sort_index()


def next_rebalance(today):
    for m in range(0, 15):
        d = (today + pd.offsets.MonthEnd(m)).normalize()
        if d.month in (1, 4, 7, 10):
            bd = pd.bdate_range(d - pd.Timedelta(days=7), d)[-1]
            if bd >= today.normalize():
                return bd
    return today


def stats(e):
    e = e.dropna()
    r = e.pct_change().dropna()
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    return {"cagr": (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1, "vol": r.std() * np.sqrt(252),
            "sharpe": r.mean() / r.std() * np.sqrt(252), "maxdd": (e / e.cummax() - 1).min(),
            "total": e.iloc[-1] / e.iloc[0] - 1}


def notes(ctx):
    """Separa el contexto del semáforo en ítems con tono: pos (a favor), neg (en contra), warn (aviso)."""
    if not isinstance(ctx, str) or not ctx.strip() or ctx.strip() == "ETF":
        return [{"tone": "", "text": "ETF: sin semáforo fundamental"}] if ctx == "ETF" else []
    out = []
    for t in [x.strip() for x in ctx.split(";") if x.strip()]:
        t = t[0].upper() + t[1:]
        low = t.lower()
        tone = ("warn" if "balance" in low else
                "neg" if any(w in low for w in ["baja", "bajan", "sin ganancias", "negativo", "debajo"]) else
                "pos" if any(w in low for w in ["alta", "suben", "arriba"]) else "")
        out.append({"tone": tone, "text": t})
    return out


def signal_files():
    return sorted(f for f in glob.glob(os.path.join(ROOT, "signals", "*.csv")) if os.path.basename(f)[:4].isdigit())


def load_dataroma():
    fp = os.path.join(ROOT, "data", "dataroma", "dataroma.json")
    if not os.path.exists(fp):
        return {}, None
    d = json.load(open(fp))
    qs = [v["activity"]["quarter"] for v in d.values() if v.get("activity") and v["activity"].get("quarter")]
    key = lambda q: (int(q.split()[1]), int(q[1]))
    return d, (max(qs, key=key) if qs else None)


def dataroma_row(d, latest_q):
    if not d:
        return None
    a = d.get("activity") or {}
    fresh = a.get("quarter") == latest_q
    return {"count": d.get("count"), "rank": d.get("rank"), "pct": d.get("pct"), "holdPrice": d.get("holdPrice"),
            "insiderBuys": d.get("insider_buys"), "insiderSells": d.get("insider_sells"),
            "quarter": latest_q, "buy": (a.get("buys", 0) + a.get("adds", 0)) if fresh else 0,
            "sell": (a.get("sells", 0) + a.get("reduces", 0)) if fresh else 0,
            "buyers": a.get("buyers", []) if fresh else [], "sellers": a.get("sellers", []) if fresh else []}


def export_signal():
    files = signal_files()
    if not files:
        return None
    s = pd.read_csv(files[-1])
    date = os.path.basename(files[-1])[:-4]
    held = held_tickers()
    picks = s.loc[s.en_cartera.astype(str).str.contains("✅"), "ticker"].tolist()
    dr, dq = load_dataroma()
    rows = []
    for _, r in s.iterrows():
        c = close(r.ticker)
        spark = []
        if c is not None:
            w = c.loc[c.index >= c.index[-1] - pd.Timedelta(days=372)].resample("W-FRI").last().dropna()
            spark = (w / w.iloc[0]).round(4).tolist()
        rows.append({"rank": int(r.puesto), "ticker": r.ticker, "score": r.score, "e4": r.E4,
                     "surprise": None if pd.isna(r["sorpresa_%"]) else r["sorpresa_%"] / 100,
                     "ret3m": r.ret_3m, "vsHigh": r.dist_max_52s, "pick": r.ticker in picks,
                     "held": r.ticker in held, "light": r.semaforo,
                     "notes": notes(r.contexto), "dataroma": dataroma_row(dr.get(r.ticker), dq),
                     "last": None if c is None else c.iloc[-1], "spark": spark})
    sell = [t for t in held if t not in picks]
    buy = [t for t in picks if t not in held]
    return {"date": date, "k": K, "picks": picks, "held": held, "buy": buy, "sell": sell,
            "nextRebalance": str(next_rebalance(pd.Timestamp(date)).date()), "dataromaQuarter": dq, "rows": rows}


def export_history():
    """Cartera objetivo de cada señal publicada y cómo le fue desde entonces (seguimiento en vivo)."""
    out = []
    for f in signal_files():
        s = pd.read_csv(f)
        d = pd.Timestamp(os.path.basename(f)[:-4])
        picks = s.loc[s.en_cartera.astype(str).str.contains("✅"), "ticker"].tolist()
        rets = {}
        for t in picks + ["SPY"]:
            c = close(t)
            if c is None or c.index[-1] <= d:
                continue
            base = c.loc[:d]
            if base.empty:
                continue
            rets[t] = c.iloc[-1] / base.iloc[-1] - 1
        port = [rets[t] for t in picks if t in rets]
        out.append({"date": str(d.date()), "picks": picks,
                    "portRet": float(np.mean(port)) if port else None, "spyRet": rets.get("SPY")})
    return out


def export_backtest():
    if not os.path.exists(CURVES):
        return None
    cv = pd.read_csv(CURVES, index_col=0, parse_dates=True)
    spy = close("SPY")
    cv["SPY"] = (spy.reindex(cv.index).ffill() / spy.reindex(cv.index).ffill().iloc[0])
    names = {"E4+sorpresa k=5": "model5", "E4+sorpresa k=8": "model8", "B&H EW30": "ew30", "SPY": "spy"}
    cv = cv[list(names)].rename(columns=names)
    weekly = cv.resample("W-FRI").last().dropna()
    yearly = cv.resample("YE").last()
    yearly = pd.concat([cv.iloc[[0]], yearly]).pct_change().dropna()
    return {"from": str(cv.index[0].date()), "to": str(cv.index[-1].date()),
            "labels": {"model5": "Modelo top 5", "model8": "Modelo top 8", "ew30": "B&H equiponderado 30", "spy": "SPY"},
            "stats": {k: stats(cv[k]) for k in cv},
            "dates": [str(d.date()) for d in weekly.index],
            "curves": {k: weekly[k].round(4).tolist() for k in weekly},
            "years": [{"year": int(d.year), **{k: yearly.loc[d, k] for k in yearly}} for d in yearly.index]}


def export_mine():
    """Operaciones reales y posiciones abiertas (valuadas con el precio del subyacente cuando hay ratio)."""
    t = load_trades()
    if t.empty:
        return {"trades": [], "positions": []}
    allpos = positions(t)
    pos = allpos[allpos.cantidad > 0]
    out = []
    for tk, p in pos.iterrows():
        c = close(tk)
        last = None if c is None else float(c.iloc[-1])
        ratio = _ratio_on(_ratios(), tk, pd.Timestamp.today()) or p.ratio  # ratio vigente hoy
        shares = p.cantidad / ratio if ratio else None
        val = shares * last if shares and last else None
        cost = p.costo_usd if p.usd_ok else None
        out.append({"ticker": tk, "qty": p.cantidad, "since": str(p.desde.date()), "costArs": p.costo_ars,
                    "costUsd": cost, "valueUsd": val, "pnlUsd": (val - cost) if val and cost else None,
                    "pnlPct": (val / cost - 1) if val and cost else None, "realizedUsd": p.realizado_usd})
    tr = [{"date": str(r.fecha.date()), "ticker": r.ticker, "op": r.operacion, "qty": r.cantidad,
           "priceArs": r.precio_ars, "ccl": r.ccl, "cclImplied": bool(r.ccl_implicito),
           "usd": r.cantidad * r.precio_ars / r.ccl if pd.notna(r.ccl) and r.ccl else None, "note": "" if pd.isna(r.get("nota")) else str(r.nota)}
          for _, r in t.sort_values("fecha", ascending=False).iterrows()]
    return {"trades": tr, "positions": out, "realizedUsd": float(allpos.realizado_usd.sum())}


def main():
    os.makedirs(OUT, exist_ok=True)
    payload = {"signal": export_signal(), "history": export_history(), "backtest": export_backtest(), "mine": export_mine(),
               "generated": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%MZ")}
    with open(os.path.join(OUT, "dashboard.json"), "w") as fh:
        json.dump(clean(payload), fh, ensure_ascii=False, separators=(",", ":"))
    print("web/data/dashboard.json OK", os.path.getsize(os.path.join(OUT, "dashboard.json")) // 1024, "KB")


if __name__ == "__main__":
    main()

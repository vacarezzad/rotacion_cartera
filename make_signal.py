"""Señal vigente del modelo de rotación + semáforo fundamental.

Modelo (validado walk-forward 2012–2026, ver README):
  score = 0,75 · rank(E4) + 0,25 · rank(sorpresa del último earnings)
  E4 = ensamble LightGBM + Ridge que predice el retorno a 63 días relativo a la mediana del universo (30 CEDEARs).
  Cartera: top 5 equiponderado, rebalanceo trimestral (último día hábil de ene/abr/jul/oct), buffer: lo que ya
  tenés se mantiene mientras siga en el top 7.
Semáforo (NO entra en el score, es contexto): calidad EDGAR, revisiones de EPS, rentabilidad, consenso, próximo earnings.

Uso: python make_signal.py [--k 5] [--portfolio portfolio.json]
Salida: signals/latest.md, signals/<fecha>.md y signals/<fecha>.csv
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "experiments"))
from tml.config import Config
from tml import data as D, features as F, model as M
from tml.edgar import edgar_panel
from exp_30f import earnings_features

UNIVERSE = ["NVDA", "GOOGL", "AMD", "QQQ", "MELI", "VIST", "META", "MSFT", "AMZN", "GLD", "AVGO", "MCD", "TSM",
            "ORCL", "TSLA", "MRNA", "GLOB", "AAPL", "KO", "BABA", "INTC", "PEP", "PBR", "V", "IBM", "WMT", "JPM",
            "ASML", "MRVL", "UNH"]
H = 63
W_E4, W_SURP = 0.75, 0.25
QUALITY = {"f_accruals": -1, "f_lev": -1, "f_fcf_yield": 1, "f_rev_g": 1, "f_om_chg": 1, "f_gm": 1}


def fit_predict_latest(X: pd.DataFrame, y: pd.Series, cfg: Config) -> pd.Series:
    """Entrena con todo lo disponible (purgado por el horizonte) y predice la última fecha. Mismo esquema que
    cada fold del walk-forward: validación interna de 12 meses para early stopping de LightGBM."""
    dates = X.index.get_level_values("date")
    ud = pd.DatetimeIndex(sorted(dates.unique()))
    last = ud[-1]
    gap = H + cfg.embargo_days
    train_end = ud[max(len(ud) - 1 - gap, 0)]
    val_start = train_end - pd.DateOffset(months=cfg.val_months)
    fit_end = M._bdays_before(ud, val_start, gap)
    cols = M.feature_cols(X)
    fit = (dates < fit_end) & y.notna().values
    val = (dates >= val_start) & (dates < train_end) & y.notna().values
    Xt = X.loc[dates == last, cols]
    g = lgb.LGBMRegressor(**{**cfg.lgb_params, "objective": "huber", "alpha": 0.15})
    g.fit(X.loc[fit, cols], y[fit], eval_set=[(X.loc[val, cols], y[val])],
          callbacks=[lgb.early_stopping(50, verbose=False)])
    yy = y[fit | val]
    r = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=1000.0))
    r.fit(X.loc[fit | val, cols], yy.clip(yy.quantile(0.01), yy.quantile(0.99)))
    pg = pd.Series(g.predict(Xt), index=Xt.index.get_level_values("ticker"))
    pr = pd.Series(r.predict(Xt), index=Xt.index.get_level_values("ticker"))
    return (pg.rank(pct=True) + pr.rank(pct=True)) / 2


def next_rebalance(today: pd.Timestamp) -> pd.Timestamp:
    for m in range(0, 15):
        d = (today + pd.offsets.MonthEnd(m)).normalize()
        if d.month in (1, 4, 7, 10):
            bd = pd.bdate_range(d - pd.Timedelta(days=7), d)[-1]
            if bd >= today.normalize():
                return bd
    return today


def semaforo(t, edgar_last, edgar_rank, snap, details, today):
    pos, neg, notes = 0, 0, []
    q = edgar_rank.get(t, np.nan)
    if not np.isnan(q):
        if q >= 0.6:
            pos += 1; notes.append("Calidad contable alta (SEC EDGAR)")
        elif q <= 0.4:
            neg += 1; notes.append("Calidad contable baja (SEC EDGAR)")
    d = details.get(t) or {}
    et = (d.get("eps_trend") or {}).get("+1y") or (d.get("eps_trend") or {}).get("0y")
    if isinstance(et, dict) and et.get("current") and et.get("30daysAgo"):
        rev = et["current"] / et["30daysAgo"] - 1 if et["30daysAgo"] else np.nan
        if rev > 0.02:
            pos += 1; notes.append(f"Analistas suben el EPS estimado {abs(rev):.0%} en 30 días")
        elif rev < -0.02:
            neg += 1; notes.append(f"Analistas bajan el EPS estimado {abs(rev):.0%} en 30 días")
    if t in snap.index:
        s = snap.loc[t]
        if pd.notna(s.get("forwardPE")) and s["forwardPE"] < 0:
            neg += 1; notes.append("Sin ganancias esperadas (P/E forward negativo)")
        if pd.notna(s.get("operatingMargins")) and s["operatingMargins"] < 0:
            neg += 1; notes.append("Margen operativo negativo")
        if pd.notna(s.get("targetMeanPrice")) and pd.notna(s.get("currentPrice")) and s["currentPrice"]:
            up = s["targetMeanPrice"] / s["currentPrice"] - 1
            if up < 0:
                neg += 1; notes.append(f"Precio objetivo de consenso {abs(up):.0%} debajo del precio actual")
            elif up > 0.3:
                pos += 1; notes.append(f"Precio objetivo de consenso {up:.0%} arriba del precio actual")
    ed = d.get("earnings_dates") or {}
    fut = sorted(pd.Timestamp(k[:10]) for k, v in ed.items() if (v or {}).get("Reported EPS") is None)
    fut = [x for x in fut if x >= today.normalize()]
    if fut and (fut[0] - today.normalize()).days <= 21:
        notes.append(f"Presenta balance el {fut[0]:%d/%m/%Y}")
    color = "🔴" if neg >= 2 or (neg >= 1 and pos == 0) else ("🟢" if pos >= 2 and neg == 0 else "🟡")
    return color, "; ".join(notes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--portfolio", default=os.path.join(ROOT, "portfolio.json"))
    a = ap.parse_args()
    cfg = Config()
    p = D.download(UNIVERSE + ["SPY", "^VIX", "^TNX"], "2003-01-01", None, os.path.join(ROOT, "data", "prices"))
    assets = [t for t in UNIVERSE if t in p["close"].columns]
    X = F.build_features(p, assets, bench="SPY")
    X = X[X["xr_ret_21"].notna() & X["tz_ret_21"].notna()]
    o = p["open"][assets]
    f = (o.shift(-(H + 1)) / o.shift(-1) - 1).stack(future_stack=True)
    f.index.names = ["date", "ticker"]
    f = f.reindex(X.index)
    y = f - f.groupby(level="date").transform("median")
    today = X.index.get_level_values("date").max()

    e4 = fit_predict_latest(X, y, cfg)
    E = earnings_features(os.path.join(ROOT, "data", "earnings"), assets, p["close"].index)
    surp = E.xs(E.index.get_level_values("date").max(), level="date")
    rs = surp["xr_e_surp"].reindex(e4.index).fillna(0.5)
    score = (W_E4 * e4.rank(pct=True) + W_SURP * rs).sort_values(ascending=False)

    held = []
    if os.path.exists(a.portfolio):
        held = json.load(open(a.portfolio)).get("held", [])
    keep = [t for t in held if t in score.index[: a.k + 2]]
    picks = (keep + [t for t in score.index if t not in keep])[: a.k]

    EP = edgar_panel(os.path.join(ROOT, "data", "edgar"), assets, p["close"][assets])
    el = EP.xs(EP.index.get_level_values("date").max(), level="date")
    qrank = sum(sgn * (el[c].rank(pct=True) - 0.5) for c, sgn in QUALITY.items()) / len(QUALITY)
    qrank = qrank.where(el[list(QUALITY)].notna().sum(axis=1) >= 3).rank(pct=True)
    snap = pd.read_csv(os.path.join(ROOT, "data", "fundamentals.csv")).set_index("ticker")
    details = {}
    for t in assets:
        fp = os.path.join(ROOT, "data", "fund_detail", f"{t}.json")
        if os.path.exists(fp):
            details[t] = json.load(open(fp))

    rows = []
    for i, t in enumerate(score.index):
        c = p["close"][t]
        color, notes = semaforo(t, el, qrank, snap, details, today) if t in el.index or t in snap.index else ("⚪", "ETF")
        rows.append({"puesto": i + 1, "ticker": t, "score": round(score[t], 3), "E4": round(e4[t], 3),
                     "sorpresa_%": surp["e_surp"].get(t), "ret_3m": c.iloc[-1] / c.iloc[-64] - 1,
                     "dist_max_52s": c.iloc[-1] / c.iloc[-252:].max() - 1,
                     "en_cartera": "✅" if t in picks else ("mantener" if t in keep else ""),
                     "semaforo": color, "contexto": notes})
    out = pd.DataFrame(rows)
    os.makedirs(os.path.join(ROOT, "signals"), exist_ok=True)
    tag = today.strftime("%Y-%m-%d")
    out.to_csv(os.path.join(ROOT, "signals", f"{tag}.csv"), index=False)

    nr = next_rebalance(pd.Timestamp.today())
    md = [f"# Señal al {tag}", "",
          f"**Cartera objetivo (top {a.k}, 20% c/u):** {', '.join(picks)}", "",
          f"Próximo rebalanceo: **{nr.date()}** (al cierre; ejecutar a la apertura siguiente). "
          f"Cartera actual en portfolio.json: {', '.join(held) if held else '(vacía)'}", "",
          "El semáforo es contexto fundamental: no cambia el ranking. 🟢 respaldo fundamental · 🟡 mixto · 🔴 alerta", "",
          "| # | Ticker | Score | E4 | Sorpresa | 3 meses | vs máx 52s | Cartera | Semáforo | Contexto |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows[:12]:
        s = "" if pd.isna(r["sorpresa_%"]) else f"{r['sorpresa_%']:+.1f}%"
        md.append(f"| {r['puesto']} | {r['ticker']} | {r['score']:.3f} | {r['E4']:.2f} | {s} | {r['ret_3m']:+.0%} | "
                  f"{r['dist_max_52s']:+.0%} | {r['en_cartera']} | {r['semaforo']} | {r['contexto']} |")
    txt = "\n".join(md) + "\n"
    for name in [f"{tag}.md", "latest.md"]:
        open(os.path.join(ROOT, "signals", name), "w").write(txt)
    print(txt)


if __name__ == "__main__":
    main()

"""E4 con 30 CEDEARs + features fundamentales point-in-time de earnings (historia yfinance desde ~2002).

Cada evento de earnings se considera conocido recién al cierre del día hábil SIGUIENTE a su fecha (conservador:
cubre reportes after-market). Features por activo y fecha:
  e_surp_last   sorpresa % del último reporte (clip ±50)
  e_surp_avg4   promedio de las últimas 4 sorpresas
  e_beat_streak trimestres consecutivos superando la estimación
  e_eps_yoy     crecimiento del EPS reportado vs 4 trimestres antes (clip ±3)
  e_days_since  días hábiles desde el último reporte (el drift post-earnings decae)
  + ranks cross-seccionales de sorpresa y crecimiento.
ETFs (QQQ, GLD) quedan en NaN (LightGBM los maneja; Ridge imputa mediana).
Uso: python exp_30f.py --data data_etf --earn data_etf/earnings --base results_30/preds30.pkl --out results_30f
"""
import argparse
import os
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tml.config import Config
from tml import data as D, features as F, model as M, backtest as B
from exp_mejoras import ens, fwd_open_to_open
from exp_v3 import run_weights, Strat, walk_forward_reg
from exp_30 import U15, U30


def log(*a):
    print(*a, flush=True)


def earnings_features(earn_dir, assets, idx):
    out = {}
    for t in assets:
        path = os.path.join(earn_dir, f"{t}.csv")
        if not os.path.exists(path):
            continue
        e = pd.read_csv(path)
        e["date"] = pd.to_datetime(e.iloc[:, 0].astype(str).str[:10])
        e = e.dropna(subset=["Reported EPS"]).sort_values("date").drop_duplicates("date")
        if e.empty:
            continue
        e["surp"] = e["Surprise(%)"].clip(-50, 50)
        e["avg4"] = e["surp"].rolling(4, min_periods=2).mean()
        beat = (e["Reported EPS"] > e["EPS Estimate"]).astype(int)
        e["streak"] = beat.groupby((beat == 0).cumsum()).cumsum()
        prev = e["Reported EPS"].shift(4)
        e["eps_yoy"] = ((e["Reported EPS"] - prev) / prev.abs().clip(lower=0.05)).clip(-3, 3)
        # disponible desde el día hábil siguiente al reporte
        pos = idx.searchsorted(e["date"].values, side="right")
        e = e[pos < len(idx)]
        e["avail"] = idx[pos[pos < len(idx)]]
        e = e.drop_duplicates("avail", keep="last").set_index("avail")
        f = e[["surp", "avg4", "streak", "eps_yoy"]].reindex(idx).ffill()
        last = pd.Series(np.where(idx.isin(e.index), np.arange(len(idx)), np.nan), index=idx).ffill()
        f["days_since"] = np.arange(len(idx)) - last
        f.loc[f["days_since"] > 130, ["surp", "avg4", "streak", "eps_yoy"]] = np.nan   # datos viejos/faltantes
        out[t] = f
    panel = pd.concat(out, names=["ticker", "date"]).swaplevel().sort_index()
    panel.columns = [f"e_{c}" for c in panel.columns]
    for c in ["e_surp", "e_avg4", "e_eps_yoy"]:
        panel[f"xr_{c}"] = panel[c].groupby(level="date").rank(pct=True)
    return panel


def ic_of(P, col, f):
    d = P[[col]].join(f.rename("f")).dropna()
    ic = d.groupby(level="date").apply(lambda g: spearmanr(g[col], g.f)[0] if len(g) > 5 else np.nan)
    return ic.mean(), (ic > 0).mean()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data_etf")
    ap.add_argument("--earn", default="data_etf/earnings")
    ap.add_argument("--base", default="results_30/preds30.pkl")
    ap.add_argument("--out", default="results_30f")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cfg = Config()
    p = D.download(U30 + ["SPY", "^VIX", "^TNX"], "2003-01-01", None, a.data)
    assets = [t for t in U30 if t in p["close"].columns]
    X = F.build_features(p, assets, bench="SPY")
    X = X[X["xr_ret_21"].notna() & X["tz_ret_21"].notna()]
    E = earnings_features(a.earn, assets, p["close"].index)
    X = X.join(E, how="left")
    cov = X["e_surp"].notna().groupby(X.index.get_level_values("date").year).mean()
    log("[30f] cobertura de features de earnings por año:\n", cov.loc[2005:].round(2).to_string())

    f63 = fwd_open_to_open(p, assets, 63).stack(future_stack=True).reindex(X.index)
    y63 = f63 - f63.groupby(level="date").transform("median")
    c63 = Config(**{**cfg.__dict__, "horizon": 63})
    log("[30f] regresión 63d con fundamentals")
    rg = walk_forward_reg(X, y63, c63, "lgb")
    rr = walk_forward_reg(X, y63, c63, "ridge")
    Pf = ens([rg, rr])["prob"].rename("reg63_fund").to_frame()
    Pb = pd.read_pickle(a.base)[["reg63"]]
    P = Pb.join(Pf, how="inner")
    P.to_pickle(f"{a.out}/preds30f.pkl")
    for col in ["reg63", "reg63_fund"]:
        m, pos = ic_of(P, col, f63)
        log(f"[30f] IC {col}: {m:.4f} (IC>0 en {100 * pos:.0f}%)")

    # importancia: correlación univariada (IC) de cada feature de earnings con el retorno relativo futuro
    for c in [c for c in X.columns if c.startswith(("e_", "xr_e_"))]:
        d = X[[c]].join(f63.rename("f")).dropna()
        d = d[d.index.get_level_values("date") >= "2012"]
        ic = d.groupby(level="date").apply(lambda g: spearmanr(g[c], g.f)[0] if len(g) > 5 else np.nan)
        log(f"   IC univariado {c:16s}: {ic.mean():+.4f}")

    S = {k: P[k].unstack().reindex(columns=assets) for k in P.columns}
    start = P.index.get_level_values("date").min()
    end = p["close"].index[-1]
    bh_spy = B.buy_and_hold(p, ["SPY"], start, end)
    bh30 = B.buy_and_hold(p, assets, start, end)
    curves = {"B&H SPY": bh_spy, "B&H equiponderado 30": bh30}
    for k in [5, 8]:
        for col, lab in [("reg63", "E4 base"), ("reg63_fund", "E4 + fundamentals")]:
            e, to = run_weights(Strat(S[col], k=k), p, assets, start, cfg, every=3)
            curves[f"{lab} k={k}"] = e
    m = pd.DataFrame(curves).resample("ME").last().pct_change().dropna()
    rows = []
    for n, e in curves.items():
        r = e.pct_change().dropna()
        yrs = (e.index[-1] - e.index[0]).days / 365.25
        ex = m[n] - m["B&H equiponderado 30"]
        h1, h2 = e.loc["2012":"2018"], e.loc["2019":]
        rows.append({"estrategia": n, "CAGR": e.iloc[-1] ** (1 / yrs) - 1, "Sharpe": r.mean() / r.std() * np.sqrt(252),
                     "MaxDD": (e / e.cummax() - 1).min(),
                     "CAGR_12_18": (h1.iloc[-1] / h1.iloc[0]) ** (365.25 / (h1.index[-1] - h1.index[0]).days) - 1,
                     "CAGR_19_26": (h2.iloc[-1] / h2.iloc[0]) ** (365.25 / (h2.index[-1] - h2.index[0]).days) - 1,
                     "t_vs_EW30": ex.mean() / ex.std() * np.sqrt(len(ex)) if ex.std() > 0 else np.nan})
    res = pd.DataFrame(rows)
    res.to_csv(f"{a.out}/u30f_resumen.csv", index=False)
    pd.DataFrame(curves).to_csv(f"{a.out}/u30f_curvas.csv")
    pd.set_option("display.width", 220)
    log(res.round(3).to_string(index=False))
    hoy = pd.DataFrame({k: v.iloc[-1] for k, v in S.items()}).sort_values("reg63_fund", ascending=False)
    hoy = hoy.join(E.xs(E.index.get_level_values("date").max(), level="date")[["e_surp", "e_eps_yoy", "e_days_since"]])
    hoy.to_csv(f"{a.out}/u30f_ranking_hoy.csv")
    log(hoy.round(3).to_string())


if __name__ == "__main__":
    main()

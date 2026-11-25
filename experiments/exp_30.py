"""Rotación E4/E0 con universo ampliado a 30 CEDEARs líquidos (vs el universo de 15).

Mismo protocolo que exp_v3 (walk-forward desde 2012, purging + embargo, costos IOL). Cada activo entra al
panel cuando tiene historia suficiente. Se reportan k=5 y k=8 (fijados antes de correr).
Uso: python exp_30.py --data data_etf --out results_30
"""
import argparse
import os
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tml.config import Config
from tml import data as D, features as F, model as M, backtest as B
from exp_mejoras import ens, fwd_open_to_open
from exp_v3 import run_weights, Strat, walk_forward_reg, report

U15 = ["NVDA", "GOOGL", "AMD", "QQQ", "MELI", "VIST", "META", "MSFT", "AMZN", "GLD", "AVGO", "MCD", "TSM",
       "ORCL", "TSLA"]
U30 = U15 + ["MRNA", "GLOB", "AAPL", "KO", "BABA", "INTC", "PEP", "PBR", "V", "IBM", "WMT", "JPM", "ASML",
             "MRVL", "UNH"]


def log(*a):
    print(*a, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data_etf")
    ap.add_argument("--out", default="results_30")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cfg = Config()
    p = D.download(U30 + ["SPY", "^VIX", "^TNX"], "2003-01-01", None, a.data)
    assets = [t for t in U30 if t in p["close"].columns]
    log(f"[30] activos: {len(assets)}")
    X = F.build_features(p, assets, bench="SPY")
    X = X[X["xr_ret_21"].notna() & X["tz_ret_21"].notna()]

    cache = f"{a.out}/preds30.pkl"
    if os.path.exists(cache):
        P = pd.read_pickle(cache)
    else:
        f21 = fwd_open_to_open(p, assets, 21).stack(future_stack=True).reindex(X.index)
        y21 = (f21 > f21.groupby(level="date").transform("median")).astype(float).where(f21.notna())
        c21 = Config(**{**cfg.__dict__, "horizon": 21})
        log("[30] clasificación 21d")
        pl = M.walk_forward(X, y21, c21, model="logit", log=lambda *_: None)
        pg = M.walk_forward(X, y21, c21, model="lgb", log=lambda *_: None)
        f63 = fwd_open_to_open(p, assets, 63).stack(future_stack=True).reindex(X.index)
        y63 = f63 - f63.groupby(level="date").transform("median")
        c63 = Config(**{**cfg.__dict__, "horizon": 63})
        log("[30] regresión 63d")
        rg = walk_forward_reg(X, y63, c63, "lgb")
        rr = walk_forward_reg(X, y63, c63, "ridge")
        P = pd.DataFrame({"ens21": ens([pl, pg])["prob"]}).join(ens([rg, rr])["prob"].rename("reg63"), how="outer")
        P.to_pickle(cache)
        # calidad de ranking: IC medio por fecha
        from scipy.stats import spearmanr
        for col, f in [("ens21", f21), ("reg63", f63)]:
            d = P[[col]].join(f.rename("f")).dropna()
            ic = d.groupby(level="date").apply(lambda g: spearmanr(g[col], g.f)[0] if len(g) > 5 else np.nan)
            log(f"[30] IC medio {col}: {ic.mean():.4f}  (IC>0 en {100 * (ic > 0).mean():.0f}% de fechas)")

    S = {k: P[k].unstack().reindex(columns=assets) for k in ["ens21", "reg63"]}
    mom = X["xr_mom_12_1"].unstack().reindex(columns=assets).loc[S["ens21"].index[0]:]
    start = P.index.get_level_values("date").min()
    end = p["close"].index[-1]
    specs = {}
    for k in [5, 8]:
        specs[f"E4 regresión 63d trimestral k={k}"] = (Strat(S["reg63"], k=k), 3)
        specs[f"E0 clasificación 21d bimestral k={k}"] = (Strat(S["ens21"], k=k), 2)
        specs[f"(control) momentum 12-1 bimestral k={k}"] = (Strat(mom, k=k), 2)
    bh_spy = B.buy_and_hold(p, ["SPY"], start, end)
    curves = {"B&H SPY": bh_spy, "B&H equiponderado 30": B.buy_and_hold(p, assets, start, end),
              "B&H equiponderado 15": B.buy_and_hold(p, U15, start, end)}
    turn = {}
    for n, (st, every) in specs.items():
        e, to = run_weights(st, p, assets, start, cfg, every=every)
        curves[n], turn[n] = e, to
        log(f"  {n}: listo")
    res = report(curves, turn, curves["B&H equiponderado 30"], bh_spy, a.out)
    res.to_csv(f"{a.out}/u30_resumen.csv", index=False)
    pd.DataFrame(curves).to_csv(f"{a.out}/u30_curvas.csv")
    m = pd.DataFrame(curves).resample("ME").last().pct_change().dropna()
    for n in specs:
        for b in ["B&H equiponderado 30", "B&H equiponderado 15"]:
            ex = m[n] - m[b]
            log(f"  t vs {b[-2:]}: {n}: exceso {ex.mean() * 12:.3f}/año t={ex.mean() / ex.std() * np.sqrt(len(ex)):.2f}")
    pd.set_option("display.width", 260)
    log(res.round(3).to_string(index=False))
    hoy = pd.DataFrame({k: v.iloc[-1] for k, v in S.items()}).sort_values("reg63", ascending=False)
    hoy.to_csv(f"{a.out}/u30_ranking_hoy.csv")
    log("ranking al", S["reg63"].index[-1].date(), "\n", hoy.round(4).to_string())


if __name__ == "__main__":
    main()

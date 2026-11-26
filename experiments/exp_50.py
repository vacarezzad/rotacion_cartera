"""Modelo vigente (0,75·E4 + 0,25·sorpresa) con universo ampliado a 50 CEDEARs líquidos vs el de 30.

Universo 50 = los 30 + las 20 acciones siguientes por volumen en BYMA (nov-2026) con historia previa a 2012
(salvo RIOT, desde 2016), sin ETFs y sin NU. Mismo protocolo: walk-forward desde 2012, purging + embargo, costos IOL.
Pre-declarado: k = 5 y k = 8; comparación contra B&H equiponderado de 50 y de 30, y contra el modelo con 30.
Uso: python exp_50.py --data data --out results/results_50  (cwd = raíz del repo)
"""
import argparse, os, sys, warnings
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy.stats import spearmanr
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tml.config import Config
from tml import data as D, features as F, backtest as B
from exp_mejoras import ens, fwd_open_to_open
from exp_v3 import run_weights, Strat, walk_forward_reg
from exp_30 import U30
from exp_30f import earnings_features
from exp_30e import perf, xr

NEW20 = ["RIOT", "NFLX", "CAT", "LLY", "BBD", "PG", "VALE", "QCOM", "ADBE", "NKE", "JNJ", "CRM", "AMAT", "AXP",
         "C", "GE", "MA", "ANF", "SID", "ITUB"]
U50 = U30 + NEW20


def log(*a):
    print(*a, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="results/results_50")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cfg = Config()
    p = D.download(U50 + ["SPY", "^VIX", "^TNX"], "2003-01-01", None, os.path.join(a.data, "prices"))
    assets = [t for t in U50 if t in p["close"].columns]
    log(f"[50] activos: {len(assets)}")
    X = F.build_features(p, assets, bench="SPY")
    X = X[X["xr_ret_21"].notna() & X["tz_ret_21"].notna()]
    cache = f"{a.out}/preds50.pkl"
    f63 = fwd_open_to_open(p, assets, 63).stack(future_stack=True)
    f63.index.names = ["date", "ticker"]
    f63 = f63.reindex(X.index)
    if os.path.exists(cache):
        P = pd.read_pickle(cache)
    else:
        y63 = f63 - f63.groupby(level="date").transform("median")
        c63 = Config(**{**cfg.__dict__, "horizon": 63})
        log("[50] regresión 63d: LightGBM")
        rg = walk_forward_reg(X, y63, c63, "lgb")
        log("[50] regresión 63d: Ridge")
        rr = walk_forward_reg(X, y63, c63, "ridge")
        P = ens([rg, rr])["prob"].rename("reg63").to_frame()
        P.to_pickle(cache)
    d = P.join(f63.rename("f")).dropna()
    ic = d.groupby(level="date").apply(lambda g: spearmanr(g.reg63, g.f)[0] if len(g) > 8 else np.nan)
    log(f"[50] IC medio E4: {ic.mean():.4f} (IC>0 en {100 * (ic > 0).mean():.0f}%)")
    E = earnings_features(os.path.join(a.data, "earnings"), assets, p["close"].index)
    score = (0.75 * xr(P["reg63"]) + 0.25 * E["xr_e_surp"].reindex(P.index).fillna(0.5)).unstack().reindex(columns=assets)
    start = P.index.get_level_values("date").min()
    end = p["close"].index[-1]
    bh50 = B.buy_and_hold(p, assets, start, end)
    bh30 = B.buy_and_hold(p, U30, start, end)
    curves = {"B&H EW50": bh50, "B&H EW30": bh30, "B&H SPY": B.buy_and_hold(p, ["SPY"], start, end)}
    for k in [5, 8]:
        e, _ = run_weights(Strat(score, k=k), p, assets, start, cfg, every=3)
        curves[f"Modelo 50 k={k}"] = e
    old = pd.read_csv("results/results_30f/u30f_blend_curvas.csv", index_col=0, parse_dates=True)
    for k in [5, 8]:
        curves[f"Modelo 30 k={k}"] = old[f"E4+sorpresa k={k}"].reindex(bh50.index).ffill()
    rows = []
    for n, e in curves.items():
        e = e.dropna()
        rows.append({"estrategia": n, **perf(e / e.iloc[0], bh50), "t_vs_EW30": perf(e / e.iloc[0], bh30)["t_vs_EW30"]})
    res = pd.DataFrame(rows).rename(columns={"t_vs_EW30": "t_vs_EW30"})
    res.to_csv(f"{a.out}/u50_resumen.csv", index=False)
    pd.DataFrame(curves).to_csv(f"{a.out}/u50_curvas.csv")
    pd.set_option("display.width", 240)
    log(res.round(3).to_string(index=False))
    # ¿cuánto peso tienen las nuevas en los picks?
    st = Strat(score, k=5)
    picks = []
    for dd in score.index:
        if dd.month in (1, 4, 7, 10) and dd == score.index[score.index.to_period("M") == dd.to_period("M")].max():
            w = st(dd)
            if w is not None:
                picks += list(w.index)
    s = pd.Series(picks)
    log(f"[50] % de picks top5 que son de las 20 nuevas: {s.isin(NEW20).mean():.1%}; más elegidas: {s[s.isin(NEW20)].value_counts().head(8).to_dict()}")
    hoy = score.iloc[-1].sort_values(ascending=False).head(10)
    log("[50] ranking hoy:\n" + hoy.round(3).to_string())


if __name__ == "__main__":
    main()

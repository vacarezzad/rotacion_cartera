"""E4 + sorpresa de earnings + fundamentals SEC EDGAR (point-in-time). Universo 30, 2012–2026, neto IOL.

Pre-declarado (antes de correr):
  compuesto fundamental = promedio de ranks cross-seccionales de
     + crecimiento de ventas TTM, + cambio de margen operativo, + margen bruto, + FCF yield,
     − accruals, − apalancamiento   (signos clásicos de la literatura de calidad/crecimiento/valor)
  T1 = 0,75·rank(E4) + 0,25·rank(sorpresa)                      (vigente)
  T2 = 0,60·rank(E4) + 0,20·rank(sorpresa) + 0,20·rank(compuesto)
Uso: python exp_30e.py --data data_etf --edgar data_etf/edgar
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
from tml import data as D, backtest as B
from tml.edgar import edgar_panel
from exp_mejoras import fwd_open_to_open
from exp_v3 import run_weights, Strat
from exp_30 import U30
from exp_30f import earnings_features

SIGNS = {"f_rev_g": 1, "f_om_chg": 1, "f_gm": 1, "f_fcf_yield": 1, "f_accruals": -1, "f_lev": -1}


def log(*a):
    print(*a, flush=True)


def xr(s):
    return s.groupby(level="date").rank(pct=True)


def perf(e, bh):
    r = e.pct_change().dropna()
    y = (e.index[-1] - e.index[0]).days / 365.25
    m = e.resample("ME").last().pct_change().dropna()
    mb = bh.reindex(e.index).ffill().resample("ME").last().pct_change().reindex(m.index)
    ex = m - mb
    c = lambda a, b: (lambda x: (x.iloc[-1] / x.iloc[0]) ** (365.25 / (x.index[-1] - x.index[0]).days) - 1)(e.loc[a:b])
    ya, yb = e.resample("YE").last().pct_change(), bh.reindex(e.index).ffill().resample("YE").last().pct_change()
    return {"CAGR": e.iloc[-1] ** (1 / y) - 1, "Sharpe": r.mean() / r.std() * np.sqrt(252),
            "MaxDD": (e / e.cummax() - 1).min(), "CAGR_12_18": c("2012", "2018"), "CAGR_19_26": c("2019", "2026"),
            "años_>EW": f"{int((ya > yb).sum())}/{int(ya.notna().sum())}",
            "t_vs_EW30": ex.mean() / ex.std() * np.sqrt(len(ex)) if ex.std() > 0 else np.nan}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data_etf")
    ap.add_argument("--edgar", default="data_etf/edgar")
    ap.add_argument("--out", default="results_30e")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cfg = Config()
    p = D.download(U30 + ["SPY"], "2003-01-01", None, a.data)
    C = p["close"][U30]
    EP = edgar_panel(a.edgar, U30, C)
    last = EP.xs(EP.index.get_level_values("date").max(), level="date")
    log("[30e] features EDGAR hoy (control de cordura):\n", last.round(3).to_string())
    cov = EP.notna().groupby(EP.index.get_level_values("date").year).mean().loc[2010:]
    log("[30e] cobertura por año:\n", cov.round(2).to_string())

    P = pd.read_pickle("results_30f/preds30f.pkl")[["reg63"]]
    E = earnings_features(os.path.join(a.data, "earnings"), U30, p["close"].index)
    f63 = fwd_open_to_open(p, U30, 63).stack(future_stack=True)
    f63.index.names = ["date", "ticker"]
    F = EP.reindex(P.index)
    for c in list(SIGNS) + ["f_om", "f_fcf_margin", "f_ey"]:
        d = F[[c]].join(f63.rename("f")).dropna()
        ic = d.groupby(level="date").apply(lambda g: spearmanr(g[c], g.f)[0] if len(g) > 8 else np.nan)
        log(f"   IC univariado {c:13s} {ic.mean():+.4f}  (n fechas {ic.notna().sum()})")
    comp = sum(SIGNS[c] * (xr(F[c]) - 0.5) for c in SIGNS) / len(SIGNS)
    comp = comp.where(F[list(SIGNS)].notna().sum(axis=1) >= 3)
    d = comp.rename("c").to_frame().join(f63.rename("f")).dropna()
    ic = d.groupby(level="date").apply(lambda g: spearmanr(g.c, g.f)[0] if len(g) > 8 else np.nan)
    log(f"   IC compuesto fundamental  {ic.mean():+.4f}")

    rE4 = xr(P["reg63"])
    rS = E["xr_e_surp"].reindex(P.index).fillna(0.5)
    rC = xr(comp).reindex(P.index).fillna(0.5)
    scores = {"T1 E4 + sorpresa": 0.75 * rE4 + 0.25 * rS,
              "T2 E4 + sorpresa + EDGAR": 0.60 * rE4 + 0.20 * rS + 0.20 * rC}
    start = P.index.get_level_values("date").min()
    bh = B.buy_and_hold(p, U30, start, p["close"].index[-1])
    rows, curves = [{"estrategia": "B&H equiponderado 30", **perf(bh, bh)}], {"B&H EW30": bh}
    for n, s in scores.items():
        S = s.unstack().reindex(columns=U30)
        for k in [5, 8]:
            e, _ = run_weights(Strat(S, k=k), p, U30, start, cfg, every=3)
            rows.append({"estrategia": f"{n} k={k}", **perf(e, bh)})
            curves[f"{n} k={k}"] = e
    res = pd.DataFrame(rows)
    res.to_csv(f"{a.out}/u30e_resumen.csv", index=False)
    pd.DataFrame(curves).to_csv(f"{a.out}/u30e_curvas.csv")
    pd.set_option("display.width", 220)
    log(res.round(3).to_string(index=False))
    hoy = pd.DataFrame({n: s.xs(s.index.get_level_values("date").max(), level="date") for n, s in scores.items()})
    hoy = hoy.join(last[list(SIGNS)]).sort_values("T2 E4 + sorpresa + EDGAR", ascending=False)
    hoy.to_csv(f"{a.out}/u30e_ranking_hoy.csv")
    log(hoy.round(3).head(12).to_string())


if __name__ == "__main__":
    main()

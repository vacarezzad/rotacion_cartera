"""Rotación de ETFs multi-activo (CEDEARs de ETF líquidos en BYMA). Walk-forward/reglas, neto de costos IOL.

Universo: ETFs con CEDEAR y volumen en BYMA (Supabase cedear_ccl_diario, nov-2026). Cada ETF entra cuando
tiene 1 año de historia. Liquidez = USD (rinde 0%, conservador: en IOL el USD en cuenta no paga).

Estrategias (parámetros clásicos de la literatura, fijados antes de correr):
S1 Momentum relativo: top-3 por promedio de retornos 1/3/6/12 meses, rebalanceo mensual.
S2 Dual momentum: S1, pero cada slot va a USD si el retorno 12m del ETF es negativo.
S3 Tendencia + momentum (estilo GTAA): top-3 entre los que están sobre su media de 10 meses, si no USD.
S4 S3 con pesos inverse-vol (riesgo parejo entre oro, defensivos, tech).
S5 ML: ensamble logit+LGBM sobre el panel de ETFs (target: retorno 21d sobre la mediana), con filtro de tendencia.
Benchmarks: B&H SPY, B&H QQQ, B&H equiponderado de los ETFs (sin costos).
Uso: python exp_etf.py --data data_etf --out results_etf
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
from exp_v3 import run_weights, Strat

ETFS = ["SPY", "QQQ", "DIA", "IWM", "SMH", "XLK", "XLV", "XLP", "XLU", "XLE", "XLI", "XLY", "XLB", "XLC",
        "GLD", "SLV", "GDX", "VEA", "EFA", "FXI", "USO"]


def log(*a):
    print(*a, flush=True)


def stats(e: pd.Series, spy: pd.Series):
    e = e.dropna()
    r = e.pct_change().dropna()
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    out = {"CAGR": e.iloc[-1] ** (1 / yrs) / e.iloc[0] ** (1 / yrs) - 1, "Vol": r.std() * np.sqrt(252),
           "Sharpe": r.mean() / r.std() * np.sqrt(252), "MaxDD": (e / e.cummax() - 1).min()}
    out["Calmar"] = out["CAGR"] / abs(out["MaxDD"])
    for lab, a, b in [("01_07", "2001", "2007"), ("08_12", "2008", "2012"), ("13_19", "2013", "2019"),
                      ("20_26", "2020", "2026")]:
        ee = e.loc[a:b]
        if len(ee) > 200:
            y = (ee.index[-1] - ee.index[0]).days / 365.25
            out[f"CAGR_{lab}"] = (ee.iloc[-1] / ee.iloc[0]) ** (1 / y) - 1
    ya = e.resample("YE").last().pct_change().dropna()
    ys = spy.reindex(e.index).ffill().resample("YE").last().pct_change().dropna()
    out["años_gana_SPY"] = f"{int((ya > ys.reindex(ya.index)).sum())}/{len(ya)}"
    m = e.resample("ME").last().pct_change().dropna()
    ms = spy.reindex(e.index).ffill().resample("ME").last().pct_change().reindex(m.index)
    ex = m - ms
    out["t_vs_SPY"] = ex.mean() / ex.std() * np.sqrt(len(ex))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data_etf")
    ap.add_argument("--out", default="results_etf")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cfg = Config()
    p = D.download(ETFS + ["^VIX", "^TNX"], "1998-01-01", None, a.data)
    C = p["close"][ETFS]
    # momentum compuesto y tendencia (datos al cierre de d)
    mom = sum(C.pct_change(n) for n in [21, 63, 126, 252]) / 4
    mom = mom.where(C.notna() & C.shift(252).notna())
    ret12 = C.pct_change(252)
    trend = C / C.rolling(210).mean() - 1
    vol = np.log(C).diff().rolling(63).std()
    start = pd.Timestamp("2000-01-01")
    score_trend = mom.where(trend > 0)            # S3: solo compiten los que están en tendencia

    specs = {
        "S1 momentum relativo top3": (Strat(mom, k=3), 1),
        "S2 dual momentum top3": (Strat(mom, k=3, abs_mom=ret12), 1),
        "S3 tendencia+momentum top3": (Strat(score_trend.fillna(-9), k=3, abs_mom=trend), 1),
        "S4 S3 + inverse-vol": (Strat(score_trend.fillna(-9), k=3, abs_mom=trend, weight="invvol", vol=vol), 1),
        "S3 bimestral": (Strat(score_trend.fillna(-9), k=3, abs_mom=trend), 2),
    }
    # fillna(-9) deja a los ETFs fuera de tendencia al fondo del ranking; abs_mom los manda a USD si quedan
    for st, _ in specs.values():
        st.score = st.score.where(C.shift(252).notna())

    # ---- S5 ML sobre el panel de ETFs ----
    X = F.build_features(p, ETFS, bench="SPY")
    X = X[X["xr_ret_21"].notna() & X["tz_ret_21"].notna()]
    f21 = fwd_open_to_open(p, ETFS, 21).stack(future_stack=True).reindex(X.index)
    y = (f21 > f21.groupby(level="date").transform("median")).astype(float).where(f21.notna())
    c21 = Config(**{**cfg.__dict__, "horizon": 21, "first_test_year": 2006})
    log("[etf] walk-forward ML")
    pl = M.walk_forward(X, y, c21, model="logit", log=lambda *_: None)
    pg = M.walk_forward(X, y, c21, model="lgb", log=lambda *_: None)
    ml = ens([pl, pg])["prob"].unstack().reindex(columns=ETFS)
    from sklearn.metrics import roc_auc_score
    d = ens([pl, pg]).join(y.rename("y")).dropna()
    log(f"[etf] AUC OOS ML = {roc_auc_score(d.y, d.prob):.3f}")
    specs["S5 ML + filtro tendencia top3"] = (Strat(ml.where(trend.reindex(ml.index) > 0).fillna(-9)
                                                     .where(ml.notna()), k=3, abs_mom=trend), 1)
    specs["(control) momentum top3 desde 2006"] = (Strat(mom.loc["2006":], k=3), 1)

    end = C.index[-1]
    curves, turn = {}, {}
    for n, (st, every) in specs.items():
        s0 = max(start, st.score.dropna(how="all").index[0])
        e, to = run_weights(st, p, ETFS, s0, cfg, every=every)
        curves[n], turn[n] = e, to
        log(f"  {n}: listo desde {s0.date()}")
    # benchmarks
    curves["B&H SPY"] = B.buy_and_hold(p, ["SPY"], start, end)
    curves["B&H QQQ"] = B.buy_and_hold(p, ["QQQ"], start, end)
    curves["B&H equiponderado ETFs"] = B.buy_and_hold(p, ETFS, start, end)
    spy = curves["B&H SPY"]

    rows = []
    for win in ["2001", "2006", "2012"]:
        for n, e in curves.items():
            ee = e.loc[win:]
            if ee.index[0].year > int(win):
                continue
            rows.append({"desde": win, "estrategia": n, **stats(ee, spy), "turnover_anual": turn.get(n)})
    res = pd.DataFrame(rows)
    res.to_csv(f"{a.out}/etf_resumen.csv", index=False)
    pd.DataFrame(curves).to_csv(f"{a.out}/etf_curvas.csv")
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 30)
    log(res.round(3).to_string(index=False))
    hoy = pd.DataFrame({"mom": mom.iloc[-1], "tendencia": trend.iloc[-1], "ret12": ret12.iloc[-1],
                        "ml": ml.iloc[-1]}).sort_values("mom", ascending=False)
    hoy.to_csv(f"{a.out}/etf_ranking_hoy.csv")
    log("ranking al", C.index[-1].date(), "\n", hoy.round(3).to_string())


if __name__ == "__main__":
    main()

"""v3: cinco maneras distintas de intentar ganarle al buy & hold (todo walk-forward, neto de costos IOL).

Base de comparación: B&H equiponderado de los 15 activos (rebalanceo mensual SIN costos, favorece al B&H) y B&H SPY.

E0  Referencia v2: ensamble logit+LGBM (target rotación 21d), top-5, rebalanceo bimestral, equiponderado.
E1  Señal combinada: rank promedio de ensamble ML + momentum 12-1 (dos señales poco correlacionadas).
E2  Gestión de riesgo: picks de E1 con pesos inverse-vol y vol-target 25% anual (resto en USD cash).
E3  Core-satellite: 50% B&H equiponderado de los 15 + 50% top-5 de E1 (rotación baja).
E4  Regresión a 63 días: LGBM + Ridge sobre retorno futuro trimestral relativo a la mediana, rebalanceo trimestral.
E5  Dual momentum: picks de E1, pero cada slot va a cash si el propio activo tiene momentum 12-1 negativo.

Hiperparámetros fijados ANTES de correr (sin grilla) para no sumar data snooping.
Uso: python exp_v3.py [--out results_v3]
"""
import argparse
import os
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tml.config import Config
from tml import data as D, features as F, model as M, backtest as B
from exp_mejoras import ens, fwd_open_to_open


def log(*a):
    print(*a, flush=True)


def xrank(s: pd.Series) -> pd.Series:
    return s.groupby(level="date").rank(pct=True)


# ------------------------------------------------------------------ motor genérico de cartera
def run_weights(target_fn, p, assets, start, cfg, every=2, cost_mult=1.0):
    """target_fn(d) -> Series de pesos (suma <= 1; el resto queda en USD cash) decidido al cierre de d.
    Rebalanceo el último día hábil de cada `every` meses, ejecución a la apertura siguiente, costos por lado."""
    O = p["open"][assets]
    allD = O.index[O.index >= start]
    month_end = pd.Series(allD, index=allD).groupby([allD.year, allD.month]).last().values
    reb = set(pd.DatetimeIndex(month_end)[::every])
    c = cfg.cost_per_side * cost_mult
    w = pd.Series(0.0, index=assets)
    val, prev, pending, eq, to_tot = 1.0, None, None, [], 0.0
    for d in allD:
        o = O.loc[d]
        if prev is not None:
            r = (o / prev - 1).fillna(0)
            g = 1 + (w * r).sum()
            val *= g
            w = w * (1 + r) / g
        if pending is not None:
            to = (pending - w).abs().sum()
            val *= 1 - to * c
            to_tot += to
            w, pending = pending, None
        prev = o
        eq.append((d, val))
        if d in reb:
            t = target_fn(d)
            if t is not None:
                pending = t.reindex(assets).fillna(0.0)
    e = pd.Series(dict(eq)).sort_index()
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    return e, to_tot / yrs


def topk_picks(score_row: pd.Series, held: list, k=5, buffer=2):
    s = score_row.dropna().sort_values(ascending=False)
    keep = [t for t in held if t in s.index[: k + buffer]]
    return (keep + [t for t in s.index if t not in keep])[:k]


class Strat:
    """Arma target_fn con memoria de lo que está en cartera (para el buffer de rotación)."""

    def __init__(self, score, k=5, weight="ew", vol=None, cov_rets=None, vol_target=None,
                 abs_mom=None, core=0.0, assets=None):
        self.score, self.k, self.weight, self.vol = score, k, weight, vol
        self.cov_rets, self.vol_target, self.abs_mom, self.core = cov_rets, vol_target, abs_mom, core
        self.assets, self.held = assets, []

    def __call__(self, d):
        if d not in self.score.index:
            return None
        row = self.score.loc[d]
        picks = topk_picks(row, self.held, self.k)
        if not picks:
            return None
        self.held = picks
        if self.weight == "invvol":
            iv = 1 / self.vol.loc[d, picks].clip(lower=1e-4)
            w = iv / iv.sum()
        else:
            w = pd.Series(1.0 / len(picks), index=picks)
        if self.abs_mom is not None:                      # dual momentum: slot a cash si mom propio < 0
            ok = self.abs_mom.loc[d, picks] > 0
            w = w * ok.astype(float)
        if self.vol_target is not None:
            R = self.cov_rets.loc[:d].iloc[-126:][picks].fillna(0)
            pv = float(np.sqrt(w.values @ R.cov().values @ w.values * 252))
            w = w * min(1.0, self.vol_target / pv) if pv > 0 else w
        if self.core > 0:
            avail = row.dropna().index
            core = pd.Series(1.0 / len(avail), index=avail)
            w = (self.core * core).add((1 - self.core) * w, fill_value=0.0)
        return w


# ------------------------------------------------------------------ regresión walk-forward
def walk_forward_reg(X, y, cfg, model="lgb"):
    dates = X.index.get_level_values("date")
    udates = pd.DatetimeIndex(sorted(dates.unique()))
    gap = cfg.horizon + cfg.embargo_days
    starts = pd.date_range(f"{cfg.first_test_year}-01-01", udates[-1], freq=f"{cfg.retrain_months}MS")
    cols = M.feature_cols(X)
    out = []
    for i, ts in enumerate(starts):
        te = starts[i + 1] if i + 1 < len(starts) else udates[-1] + pd.Timedelta(days=1)
        test_m = (dates >= ts) & (dates < te)
        if not test_m.any():
            continue
        train_end = M._bdays_before(udates, ts, gap)
        val_start = ts - pd.DateOffset(months=cfg.val_months)
        fit_end = M._bdays_before(udates, val_start, gap)
        fit_m = (dates < fit_end) & y.notna().values
        val_m = (dates >= val_start) & (dates < train_end) & y.notna().values
        if fit_m.sum() < 5000 or val_m.sum() < 500:
            continue
        Xf, yf, Xv, yv, Xt = X.loc[fit_m, cols], y[fit_m], X.loc[val_m, cols], y[val_m], X.loc[test_m, cols]
        if model == "lgb":
            prm = {**cfg.lgb_params, "objective": "huber", "alpha": 0.15}
            m = lgb.LGBMRegressor(**prm)
            m.fit(Xf, yf, eval_set=[(Xv, yv)], callbacks=[lgb.early_stopping(50, verbose=False)])
        else:
            m = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=1000.0))
            m.fit(Xf, yf.clip(yf.quantile(0.01), yf.quantile(0.99)))
        out.append(pd.DataFrame({"prob": m.predict(Xt)}, index=Xt.index))
    return pd.concat(out)


# ------------------------------------------------------------------ reporte
def report(curves: dict, turn: dict, bh_ew, bh_spy, out):
    rows = []
    for n, e in curves.items():
        m = B.metrics(e, bh_spy)
        yr = e.resample("YE").last().pct_change()
        yr.iloc[0] = e.resample("YE").last().iloc[0] / e.iloc[0] - 1
        ew = bh_ew.reindex(e.index).ffill()
        yr_ew = ew.resample("YE").last().pct_change()
        yr_ew.iloc[0] = ew.resample("YE").last().iloc[0] / ew.iloc[0] - 1
        half = {}
        for lab, a, b in [("2012_18", "2012", "2018"), ("2019_26", "2019", "2026")]:
            ee = e.loc[a:b]
            yrs = (ee.index[-1] - ee.index[0]).days / 365.25
            half[f"CAGR_{lab}"] = (ee.iloc[-1] / ee.iloc[0]) ** (1 / yrs) - 1
        rows.append({"estrategia": n, **m, **half,
                     "años_gana_a_EW": f"{int((yr > yr_ew).sum())}/{len(yr)}",
                     "turnover_anual": turn.get(n, np.nan)})
    res = pd.DataFrame(rows)
    res.to_csv(f"{out}/v3_resumen.csv", index=False)
    pd.DataFrame(curves).to_csv(f"{out}/v3_curvas.csv")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results_v3")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cfg = Config()
    tick = list(dict.fromkeys(list(cfg.universe) + list(cfg.extra_tickers) + list(cfg.context_tickers)))
    p = D.download(tick, cfg.start, cfg.end, cfg.cache_dir)
    assets = [t for t in cfg.universe if t in p["close"].columns]
    X = F.build_features(p, assets, bench=cfg.benchmark)
    X = X[X["xr_ret_21"].notna() & X["tz_ret_21"].notna()]

    # --- target clasificación 21d (como v2) ---
    cache = f"{a.out}/preds_cache.pkl"
    if os.path.exists(cache):
        P = pd.read_pickle(cache)
    else:
        f21 = fwd_open_to_open(p, assets, 21).stack(future_stack=True).reindex(X.index)
        y_cs = (f21 > f21.groupby(level="date").transform("median")).astype(float).where(f21.notna())
        c21 = Config(**{**cfg.__dict__, "horizon": 21})
        log("[v3] walk-forward clasificación 21d")
        pl = M.walk_forward(X, y_cs, c21, model="logit", log=lambda *_: None)
        pg = M.walk_forward(X, y_cs, c21, model="lgb", log=lambda *_: None)
        P = pd.DataFrame({"ens21": ens([pl, pg])["prob"]})
        # --- target regresión 63d relativo ---
        f63 = fwd_open_to_open(p, assets, 63).stack(future_stack=True).reindex(X.index)
        y63 = f63 - f63.groupby(level="date").transform("median")
        c63 = Config(**{**cfg.__dict__, "horizon": 63})
        log("[v3] walk-forward regresión 63d")
        rg = walk_forward_reg(X, y63, c63, "lgb")
        rr = walk_forward_reg(X, y63, c63, "ridge")
        P = P.join(ens([rg, rr])["prob"].rename("reg63"), how="outer")
        P.to_pickle(cache)

    mom = X["xr_mom_12_1"].reindex(P.index)
    P["blend"] = (xrank(P["ens21"]) + mom) / 2
    S = {k: P[k].unstack() for k in ["ens21", "blend", "reg63"]}
    S["mom"] = mom.unstack()
    start = P.index.get_level_values("date").min()

    C = p["close"][assets]
    vol63 = np.log(C).diff().rolling(63).std()
    rets = C.pct_change()
    abs_mom = C.shift(21) / C.shift(252) - 1

    specs = {
        "E0 ref v2: ML top5 bimestral": (Strat(S["ens21"]), 2),
        "E1 señal ML+momentum": (Strat(S["blend"]), 2),
        "E2 E1 + inverse-vol + vol-target 25%": (Strat(S["blend"], weight="invvol", vol=vol63,
                                                    cov_rets=rets, vol_target=0.25), 2),
        "E3 core-satellite 50/50": (Strat(S["blend"], core=0.5), 2),
        "E4 regresión 63d trimestral": (Strat(S["reg63"]), 3),
        "E5 dual momentum": (Strat(S["blend"], abs_mom=abs_mom), 2),
        "(control) momentum 12-1 puro": (Strat(S["mom"]), 2),
    }
    end = C.index[-1]
    bh_spy = B.buy_and_hold(p, [cfg.benchmark], start, end)
    bh_ew = B.buy_and_hold(p, assets, start, end)
    curves = {"B&H SPY": bh_spy, "B&H equiponderado 15": bh_ew}
    turn = {}
    for n, (st, every) in specs.items():
        e, to = run_weights(st, p, assets, start, cfg, every=every)
        curves[n], turn[n] = e, to
        log(f"  {n}: listo")
    # sensibilidad a costos x2 del mejor candidato se calcula aparte
    for n in ["E1 señal ML+momentum", "E3 core-satellite 50/50"]:
        st, every = specs[n]
        st.held = []
        e, to = run_weights(st, p, assets, start, cfg, every=every, cost_mult=2.0)
        curves[n + " [costos x2]"], turn[n + " [costos x2]"] = e, to
    res = report(curves, turn, bh_ew, bh_spy, a.out)
    pd.set_option("display.width", 250)
    log(res.round(3).to_string(index=False))

    # señal vigente
    last = {k: S[k].iloc[-1].sort_values(ascending=False) for k in ["blend", "reg63"]}
    pd.DataFrame(last).to_csv(f"{a.out}/v3_ranking_hoy.csv")
    log("ranking al", S["blend"].index[-1].date(), "\n", pd.DataFrame(last).round(3).to_string())


if __name__ == "__main__":
    main()

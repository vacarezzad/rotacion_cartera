"""Experimentos de mejora sobre datos reales (todo walk-forward, sin leakage, neto de costos IOL).

A) Triple-barrier: grilla de horizonte/barreras + ensamble logit/LGBM -> AUC y expectativa neta del top decil.
B) Rotación siempre invertida: ranking cross-seccional de retorno a 21 días, top-k con buffer,
   rebalanceo mensual/bimestral, filtro de régimen opcional -> vs B&H SPY y B&H equiponderado.

Uso: python exp_mejoras.py [--out results_mejoras]
"""
import argparse
import os
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tml.config import Config
from tml import data as D, features as F, model as M, backtest as B


def log(*a):
    print(*a, flush=True)


def ens(preds):
    """Ensamble por promedio de rank (robusto a distinta calibración)."""
    r = [p["prob"].groupby(level="date").rank(pct=True) for p in preds]
    out = preds[0].copy()
    out["prob"] = sum(r) / len(r)
    return out


# ------------------------------------------------------------------ A) triple-barrier
def exp_triple_barrier(p, assets, X, cfg, out):
    rows = []
    rt_cost = 2 * cfg.cost_per_side
    for H, tp, sl in [(15, 2.0, 1.0), (10, 1.5, 1.5), (20, 2.0, 1.0), (20, 3.0, 1.5), (40, 3.0, 1.5), (60, 4.0, 2.0)]:
        c = Config(**{**cfg.__dict__, "horizon": H, "tp_atr": tp, "sl_atr": sl})
        L = F.triple_barrier(p, assets, H, tp, sl, bench=cfg.benchmark).reindex(X.index)
        preds = {m: M.walk_forward(X, L.y_tb, c, model=m, log=lambda *_: None) for m in ["logit", "lgb"]}
        preds["ens"] = ens([preds["logit"], preds["lgb"]])
        for m, pr in preds.items():
            d = pr.join(L[["y_tb", "ret_tb"]]).dropna()
            top = d[d.prob >= d.groupby(level="date").prob.transform(lambda s: s.quantile(0.9))]
            q = d.prob.quantile(0.9)
            topg = d[d.prob >= q]
            rows.append({"H": H, "tp": tp, "sl": sl, "modelo": m,
                         "auc": roc_auc_score(d.y_tb, d.prob), "base_rate": d.y_tb.mean(),
                         "ret_medio_todos_neto": d.ret_tb.mean() - rt_cost,
                         "ret_top10_global_neto": topg.ret_tb.mean() - rt_cost,
                         "hit_top10_global": topg.y_tb.mean(),
                         "ret_top10_por_fecha_neto": top.ret_tb.mean() - rt_cost})
            log(f"  H={H} tp={tp} sl={sl} {m}: auc={rows[-1]['auc']:.3f} "
                f"top10 neto={rows[-1]['ret_top10_global_neto']:.4f} todos neto={rows[-1]['ret_medio_todos_neto']:.4f}")
    res = pd.DataFrame(rows)
    res.to_csv(f"{out}/A_triple_barrier_grid.csv", index=False)
    return res


# ------------------------------------------------------------------ B) rotación
def fwd_open_to_open(p, assets, h):
    o = p["open"][assets]
    return o.shift(-(h + 1)) / o.shift(-1) - 1


def rotation_backtest(score: pd.DataFrame, p, assets, cfg, k=5, buffer=2, every=1, regime=None,
                      cash_when_off="cash"):
    """score: DataFrame fecha x ticker (mayor = mejor), disponible al cierre de t.
    Rebalanceo el último día hábil de cada `every` meses; ejecución a la apertura siguiente.
    buffer: un activo en cartera se mantiene mientras esté dentro del top k+buffer (baja rotación)."""
    O = p["open"][assets]
    dates = score.index
    month_end = pd.Series(dates, index=dates).groupby([dates.year, dates.month]).last().values
    month_end = pd.DatetimeIndex(month_end)[::every]
    c = cfg.cost_per_side
    eq, w = [], pd.Series(0.0, index=assets)
    val = 1.0
    turnover_tot, n_reb = 0.0, 0
    allD = O.index
    reb_set = set(month_end)
    pending = None
    prev_open = None
    for d in allD[allD.searchsorted(dates[0]):]:
        o_today = O.loc[d]
        # valuación open-to-open con los pesos vigentes
        if prev_open is not None:
            r = (o_today / prev_open - 1).fillna(0)
            grow = 1 + (w * r).sum()
            val *= grow
            w = w * (1 + r) / grow                   # pesos como fracción del equity (resto = cash)
        if pending is not None:          # ejecutar rebalanceo a la apertura
            new_w = pending
            to = (new_w - w).abs().sum()
            val *= 1 - to * c
            turnover_tot += to
            n_reb += 1
            w = new_w
            pending = None
        prev_open = o_today
        eq.append((d, val))
        if d in reb_set and d in score.index:
            s = score.loc[d].dropna()
            s = s[O.loc[d, s.index].notna()]
            if regime is not None and not bool(regime.get(d, True)):
                new = pd.Series(0.0, index=assets)
                if cash_when_off in assets:
                    new[cash_when_off] = 1.0
            else:
                ranked = s.sort_values(ascending=False)
                keep = [t for t in w[w > 0].index if t in ranked.index[: k + buffer]]
                picks = keep + [t for t in ranked.index if t not in keep][: max(0, k - len(keep))]
                picks = picks[:k]
                new = pd.Series(0.0, index=assets)
                new[picks] = 1.0 / len(picks) if picks else 0.0
            pending = new
    e = pd.Series(dict(eq)).sort_index()
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    return e, {"turnover_anual": turnover_tot / yrs, "rebalanceos": n_reb}


def exp_rotation(p, assets, X, cfg, out):
    h = 21
    fwd = fwd_open_to_open(p, assets, h)
    fwd_l = fwd.stack(future_stack=True).reindex(X.index)
    med = fwd_l.groupby(level="date").transform("median")
    y_cs = (fwd_l > med).astype(float).where(fwd_l.notna())
    c = Config(**{**cfg.__dict__, "horizon": h})
    asset_cols = [col for col in X.columns if col.startswith(("xr_", "tz_")) or col in
                  {"atr_pct", "vol_21", "rsi_14", "dist_ma20_atr", "dist_ma200_atr", "beta_63"}]
    Xa = X[asset_cols + ["dollar_vol_60", "atr"]]
    preds = {}
    for name, XX in [("all", X), ("asset_only", Xa)]:
        for m in ["logit", "lgb"]:
            preds[f"{m}_{name}"] = M.walk_forward(XX, y_cs, c, model=m, log=lambda *_: None)
    preds["ens_asset_only"] = ens([preds["logit_asset_only"], preds["lgb_asset_only"]])
    preds["ens_all"] = ens([preds["logit_all"], preds["lgb_all"]])
    start = min(pr.index.get_level_values("date").min() for pr in preds.values())
    mom = X.loc[preds["lgb_all"].index, "xr_mom_12_1"]
    ic_rows = []
    for name, pr in list(preds.items()) + [("regla_momentum", mom.to_frame("prob"))]:
        d = pr[["prob"]].join(fwd_l.rename("f")).join(y_cs.rename("y")).dropna()
        ic = d.groupby(level="date").apply(lambda g: spearmanr(g.prob, g.f)[0] if len(g) > 4 else np.nan)
        ic_rows.append({"modelo": name, "auc": roc_auc_score(d.y, d.prob), "IC_medio": ic.mean(),
                        "IC_IR_anual": ic.mean() / ic.std() * np.sqrt(252 / h), "IC_pos_%": (ic > 0).mean()})
        log(f"  rotación {name}: auc={ic_rows[-1]['auc']:.3f} IC={ic.mean():.4f}")
    pd.DataFrame(ic_rows).to_csv(f"{out}/B_rotacion_ic.csv", index=False)

    spy = p["close"][cfg.benchmark]
    regime = (spy > spy.rolling(200).mean())
    end = p["close"].index[-1]
    bh_spy = B.buy_and_hold(p, [cfg.benchmark], start, end)
    bh_ew = B.buy_and_hold(p, assets, start, end)
    rows = [{"estrategia": "B&H SPY", **B.metrics(bh_spy, bh_spy)},
            {"estrategia": "B&H equiponderado universo (sin costos)", **B.metrics(bh_ew, bh_spy)}]
    curves = {"B&H SPY": bh_spy, "B&H EW": bh_ew}
    scores = {n: pr["prob"].unstack() for n, pr in preds.items()}
    scores["regla_momentum"] = mom.unstack()
    for n, sc in scores.items():
        for k in [3, 5]:
            for every in [1, 2]:
                for reg in [None, "ma200"]:
                    e, info = rotation_backtest(sc, p, assets, cfg, k=k, buffer=2, every=every,
                                                regime=regime if reg else None, cash_when_off="cash")
                    name = f"{n} | k={k} | cada {every}m | régimen={'MA200' if reg else 'no'}"
                    rows.append({"estrategia": name, **B.metrics(e, bh_spy), **info})
                    curves[name] = e
    res = pd.DataFrame(rows)
    res.to_csv(f"{out}/B_rotacion_backtest.csv", index=False)
    pd.DataFrame(curves).to_csv(f"{out}/B_rotacion_curvas.csv")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results_mejoras")
    ap.add_argument("--only", default="AB")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cfg = Config()
    tick = list(dict.fromkeys(list(cfg.universe) + list(cfg.extra_tickers) + list(cfg.context_tickers)))
    p = D.download(tick, cfg.start, cfg.end, cfg.cache_dir)
    assets = [t for t in cfg.universe if t in p["close"].columns]
    X = F.build_features(p, assets, bench=cfg.benchmark)
    X = X[X["xr_ret_21"].notna() & X["tz_ret_21"].notna()]
    if "A" in a.only:
        log("[A] grilla triple-barrier")
        exp_triple_barrier(p, assets, X, cfg, a.out)
    if "B" in a.only:
        log("[B] rotación cross-seccional")
        res = exp_rotation(p, assets, X, cfg, a.out)
        log(res.sort_values("Sharpe", ascending=False).head(15).round(3).to_string(index=False))


if __name__ == "__main__":
    main()

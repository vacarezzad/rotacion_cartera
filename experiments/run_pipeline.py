"""Corre el pipeline completo y deja los resultados en results/.

Uso:
    python run_pipeline.py              # datos reales (yfinance)
    python run_pipeline.py --synthetic  # random walk: control de leakage (todo debería dar AUC ~0,5)
"""
import argparse
import warnings
warnings.filterwarnings("ignore")
import os
import sys
import time
import json
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tml.config import Config
from tml import data as D, features as F, model as M, backtest as B


def log(*a):
    print(*a, flush=True)


def leakage_demo(p, ticker, out):
    """Reproduce el setup original (un activo, split aleatorio) y lo corrige paso a paso."""
    feats = F.build_features(p, [ticker] + (["SPY"] if ticker != "SPY" else []))
    X = feats.xs(ticker, level="ticker")
    X = X[[c for c in X.columns if not c.startswith("xr_") and c not in M.META]]
    c = p["close"][ticker]
    lr = np.log(c).diff()
    past5 = lr.rolling(5).sum()                    # lo que hacía calculate_cumulative_log_returns
    fwd5 = lr.rolling(5).sum().shift(-5)          # lo correcto: retorno de los próximos 5 días
    rows = []
    for name, target, split in [("original: label pasado + split aleatorio", past5, "random"),
                                ("label futuro + split aleatorio", fwd5, "random"),
                                ("label futuro + split temporal (honesto)", fwd5, "time")]:
        d = X.join((target > 0).astype(float).where(target.notna()).rename("y")).dropna(subset=["y"])
        d = d.dropna(thresh=int(0.8 * d.shape[1]))
        Xd, yd = d.drop(columns="y").fillna(d.median()), d["y"]
        if split == "random":
            Xtr, Xte, ytr, yte = train_test_split(Xd, yd, test_size=0.2, stratify=yd, random_state=42)
        else:
            cut = int(len(d) * 0.8)
            Xtr, Xte, ytr, yte = Xd.iloc[:cut - 10], Xd.iloc[cut:], yd.iloc[:cut - 10], yd.iloc[cut:]
        rf = RandomForestClassifier(n_estimators=300, max_depth=6, n_jobs=-1, random_state=42).fit(Xtr, ytr)
        rows.append({"setup": name, "auc_test": roc_auc_score(yte, rf.predict_proba(Xte)[:, 1])})
    res = pd.DataFrame(rows)
    res.to_csv(f"{out}/00_leakage_demo.csv", index=False)
    log(res.to_string(index=False))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--out", default="results")
    ap.add_argument("--quick", action="store_true", help="reentrenamiento semestral (más rápido)")
    a = ap.parse_args()
    cfg = Config()
    if a.quick:
        cfg.retrain_months = 6
    out = a.out + ("_synthetic" if a.synthetic else "")
    os.makedirs(out, exist_ok=True)
    t0 = time.time()

    # ---------------------------------------------------------------- 1. datos
    if a.synthetic:
        p = D.synthetic_panels(n_assets=15, n_days=4500)
        assets = [t for t in p["close"].columns if t.startswith("A")]
        cfg.first_test_year = 2012
    else:
        tick = list(dict.fromkeys(list(cfg.universe) + list(cfg.extra_tickers) + list(cfg.context_tickers)))
        p = D.download(tick, cfg.start, cfg.end, cfg.cache_dir)
        assets = [t for t in cfg.universe if t in p["close"].columns]
        # con historia corta (p.ej. Nasdaq ~10 años) se dejan >=4 años para el primer entrenamiento
        cfg.first_test_year = max(cfg.first_test_year, p["close"][assets].dropna(how="all").index[0].year + 4)
    log(f"[1] datos: {len(assets)} activos, {p['close'].index[0].date()} -> {p['close'].index[-1].date()}")

    # ---------------------------------------------------------------- 2. demo de leakage
    log("[2] demo de leakage (setup original vs corregido)")
    leakage_demo(p, "AAPL" if "AAPL" in p["close"].columns else assets[0], out)

    # ---------------------------------------------------------------- 3. features + labels
    X = F.build_features(p, assets, bench=cfg.benchmark)
    L = F.triple_barrier(p, assets, cfg.horizon, cfg.tp_atr, cfg.sl_atr, bench=cfg.benchmark)
    ok = X["xr_ret_21"].notna() & X["tz_ret_21"].notna()      # activo con historia suficiente
    X = X[ok]
    L = L.reindex(X.index)
    log(f"[3] panel: {len(X):,} filas, {len(M.feature_cols(X))} features; "
        f"base rate TP-primero={L.y_tb.mean():.3f}, supera-SPY={L.y_rel.mean():.3f}")

    # ---------------------------------------------------------------- 4. modelos (walk-forward)
    auc_rows, preds = [], {}
    for target in ["y_tb", "y_rel"]:
        for mdl in ["logit", "lgb"]:
            log(f"[4] walk-forward target={target} modelo={mdl}")
            pr = M.walk_forward(X, L[target], cfg, model=mdl, log=lambda *_: None)
            rep = M.auc_report(pr, L[target])
            rep.insert(0, "modelo", mdl)
            rep.insert(0, "target", target)
            auc_rows.append(rep)
            preds[(target, mdl)] = pr
            tot = rep.set_index("year").loc[["TOTAL", "CS_MEAN"], "auc"]
            log(f"    AUC OOS total={tot['TOTAL']:.4f}  AUC cross-seccional medio={tot['CS_MEAN']:.4f}")
        # baseline sin ML: rank de momentum 12-1 como "probabilidad"
        mom = X[["xr_mom_12_1"]].rename(columns={"xr_mom_12_1": "prob"}).loc[preds[(target, "lgb")].index]
        rep = M.auc_report(mom.assign(thr=np.nan), L[target])
        rep.insert(0, "modelo", "regla_momentum")
        rep.insert(0, "target", target)
        auc_rows.append(rep)
    auc = pd.concat(auc_rows)
    auc.to_csv(f"{out}/01_auc_walkforward.csv", index=False)

    # ---------------------------------------------------------------- 5. backtests
    log("[5] backtests (costo por lado = {:.2%})".format(cfg.cost_per_side))
    res, curves = [], {}
    pr0 = preds[("y_tb", "lgb")]
    start, end = pr0.index.get_level_values("date").min(), pr0.index.get_level_values("date").max()
    spy = B.buy_and_hold(p, [cfg.benchmark], start, end)
    ew = B.buy_and_hold(p, assets, start, end)
    curves["B&H SPY"], curves["B&H equiponderado universo"] = spy, ew
    res.append({"estrategia": "B&H SPY", **B.metrics(spy, spy)})
    res.append({"estrategia": "B&H equiponderado universo", **B.metrics(ew, spy)})
    # regla de momentum como baseline operable (top 10% del rank 12-1, mismo motor y costos)
    mom_pr = X.loc[pr0.index, ["xr_mom_12_1"]].rename(columns={"xr_mom_12_1": "prob"})
    mom_pr["thr"] = 0.9
    for (target, mdl), pr in list(preds.items()) + [(("y_tb", "regla_momentum"), mom_pr)]:
        if target != "y_tb":
            continue   # el motor de backtest ejecuta TP/SL: sólo aplica al target triple-barrier
        for mode in ["spy", "cash"]:
            eq, tr = B.run_backtest(pr, L, p, cfg, idle_mode=mode, bench=cfg.benchmark)
            name = f"{mdl} | ocioso={mode.upper()}"
            curves[name] = eq.equity
            res.append({"estrategia": name, **B.metrics(eq.equity, spy, tr)})
            tr.to_csv(f"{out}/trades_{mdl}_{mode}.csv", index=False)
    bt = pd.DataFrame(res)
    bt.to_csv(f"{out}/02_backtest.csv", index=False)
    pd.DataFrame(curves).to_csv(f"{out}/03_curvas_equity.csv")
    log(bt.round(4).to_string(index=False))

    # ---------------------------------------------------------------- 6. importancia de features
    try:
        import lightgbm as lgb
        fit = L.y_tb.notna()
        m = lgb.LGBMClassifier(**{**cfg.lgb_params, "n_estimators": 300}).fit(X.loc[fit, M.feature_cols(X)], L.y_tb[fit])
        imp = pd.Series(m.booster_.feature_importance("gain"), index=M.feature_cols(X)).sort_values(ascending=False)
        (imp / imp.sum()).to_csv(f"{out}/04_feature_importance_gain.csv")
    except Exception as e:
        log("importancia: ", e)

    json.dump({"cost_per_side": cfg.cost_per_side, "horizon": cfg.horizon, "tp_atr": cfg.tp_atr,
               "sl_atr": cfg.sl_atr, "assets": assets, "minutes": (time.time() - t0) / 60},
              open(f"{out}/config_usada.json", "w"), indent=2)
    log(f"listo en {(time.time() - t0) / 60:.1f} min -> {out}/")


if __name__ == "__main__":
    main()

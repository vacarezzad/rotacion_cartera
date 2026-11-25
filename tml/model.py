"""Walk-forward con purging + embargo, validación interna para early stopping y umbral."""
from __future__ import annotations
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.metrics import roc_auc_score

META = {"dollar_vol_60", "atr"}


def feature_cols(df):
    return [c for c in df.columns if c not in META]


def _bdays_before(dates: pd.DatetimeIndex, ts, n):
    """Fecha n días hábiles (del calendario del dataset) antes de ts."""
    i = dates.searchsorted(ts)
    return dates[max(i - n, 0)]


def walk_forward(X: pd.DataFrame, y: pd.Series, cfg, model="lgb", log=print) -> pd.DataFrame:
    """Devuelve predicciones fuera de muestra (prob) para cada fila de test + umbral de cada fold."""
    dates = X.index.get_level_values("date")
    udates = pd.DatetimeIndex(sorted(dates.unique()))
    gap = cfg.horizon + cfg.embargo_days
    starts = pd.date_range(f"{cfg.first_test_year}-01-01", udates[-1], freq=f"{cfg.retrain_months}MS")
    cols = feature_cols(X)
    preds = []
    for i, ts in enumerate(starts):
        te = starts[i + 1] if i + 1 < len(starts) else udates[-1] + pd.Timedelta(days=1)
        test_m = (dates >= ts) & (dates < te)
        if not test_m.any():
            continue
        train_end = _bdays_before(udates, ts, gap)                       # purging + embargo
        val_start = ts - pd.DateOffset(months=cfg.val_months)
        fit_end = _bdays_before(udates, val_start, gap)
        fit_m = (dates < fit_end) & y.notna().values
        val_m = (dates >= val_start) & (dates < train_end) & y.notna().values
        if fit_m.sum() < 5000 or val_m.sum() < 500:
            continue
        Xf, yf = X.loc[fit_m, cols], y[fit_m]
        Xv, yv = X.loc[val_m, cols], y[val_m]
        Xt = X.loc[test_m, cols]
        if model == "lgb":
            m = lgb.LGBMClassifier(**cfg.lgb_params)
            m.fit(Xf, yf, eval_set=[(Xv, yv)], eval_metric="auc",
                  callbacks=[lgb.early_stopping(50, verbose=False)])
        else:
            m = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                              LogisticRegression(C=0.05, max_iter=2000))
            m.fit(Xf, yf)
        pv = m.predict_proba(Xv)[:, 1]
        thr = float(np.quantile(pv, cfg.signal_quantile))
        auc_v = roc_auc_score(yv, pv) if yv.nunique() > 1 else np.nan
        pt = m.predict_proba(Xt)[:, 1]
        preds.append(pd.DataFrame({"prob": pt, "thr": thr, "fold": i, "auc_val": auc_v}, index=Xt.index))
        log(f"  fold {ts.date()} train<{train_end.date()} n_fit={fit_m.sum():,} auc_val={auc_v:.3f}")
    return pd.concat(preds)


def auc_report(pred: pd.DataFrame, y: pd.Series) -> pd.DataFrame:
    d = pred.join(y.rename("y")).dropna(subset=["y"])
    d["year"] = d.index.get_level_values("date").year
    rows = [{"year": "TOTAL", "auc": roc_auc_score(d.y, d.prob), "n": len(d), "base_rate": d.y.mean()}]
    for yr, g in d.groupby("year"):
        if g.y.nunique() > 1:
            rows.append({"year": yr, "auc": roc_auc_score(g.y, g.prob), "n": len(g), "base_rate": g.y.mean()})
    # AUC cross-seccional medio por fecha (lo que importa para elegir activos)
    cs = d.groupby(level="date").apply(lambda g: roc_auc_score(g.y, g.prob) if g.y.nunique() > 1 else np.nan)
    rows.append({"year": "CS_MEAN", "auc": cs.mean(), "n": cs.notna().sum(), "base_rate": np.nan})
    return pd.DataFrame(rows)

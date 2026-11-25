"""Features (todas con información disponible al cierre de t) y etiquetas (todas desde la apertura de t+1)."""
from __future__ import annotations
import numpy as np
import pandas as pd


# ----------------------------------------------------------------- utilidades
def _atr(h, l, c, n=14):
    pc = c.shift(1)
    tr = np.maximum(h - l, np.maximum((h - pc).abs(), (l - pc).abs()))
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def _rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn)


def _xrank(df):
    """Rank cross-seccional (0-1) dentro del universo de cada fecha."""
    return df.rank(axis=1, pct=True)


# ----------------------------------------------------------------- features
def build_features(p: dict, assets: list[str], bench="SPY", vix="^VIX", tnx="^TNX") -> pd.DataFrame:
    o, h, l, c, v = (p[k][assets] for k in ["open", "high", "low", "close", "volume"])
    r = np.log(c).diff()
    atr = _atr(h, l, c)
    spy = p["close"][bench]
    r_spy = np.log(spy).diff()

    f = {}
    for n in [1, 5, 21, 63, 126, 252]:
        f[f"ret_{n}"] = c.pct_change(n)
    f["mom_12_1"] = c.shift(21) / c.shift(252) - 1
    f["vol_21"] = r.rolling(21).std()
    f["vol_63"] = r.rolling(63).std()
    f["vol_ratio"] = f["vol_21"] / f["vol_63"]
    f["atr_pct"] = atr / c
    f["rsi_14"] = _rsi(c)
    for n in [20, 50, 200]:
        f[f"dist_ma{n}_atr"] = (c - c.rolling(n).mean()) / atr
    f["pos_52w"] = (c - l.rolling(252).min()) / (h.rolling(252).max() - l.rolling(252).min())
    f["dd_63"] = c / c.rolling(63).max() - 1
    f["rel_vol_20"] = v / v.rolling(20).mean()
    f["gap_1"] = o / c.shift(1) - 1
    f["range_1_atr"] = (h - l) / atr
    for n in [21, 63]:
        f[f"rs_spy_{n}"] = c.pct_change(n).sub(spy.pct_change(n), axis=0)
    cov = r.rolling(63).cov(r_spy)
    f["beta_63"] = cov.div(r_spy.rolling(63).var(), axis=0)
    f["dollar_vol_60"] = (c * v).rolling(60).mean()

    # rank cross-seccional de cada feature de activo + algunos niveles crudos
    raw_keep = {"atr_pct", "vol_21", "rsi_14", "dist_ma20_atr", "dist_ma200_atr", "beta_63"}
    cols = {}
    for k, df in f.items():
        if k == "dollar_vol_60":
            continue
        cols[f"xr_{k}"] = _xrank(df)
        if k in raw_keep:
            cols[k] = df

    # z-score temporal (vs. la propia historia del activo, 252d): con universo chico el rank
    # cross-seccional pierde resolución, así que se agregan ambas vistas
    for k in ["ret_5", "ret_21", "ret_63", "vol_21", "rel_vol_20", "dist_ma50_atr", "rs_spy_21", "atr_pct"]:
        df = f[k]
        cols[f"tz_{k}"] = ((df - df.rolling(252).mean()) / df.rolling(252).std()).clip(-5, 5)

    long = pd.concat({k: d.stack(future_stack=True) for k, d in cols.items()}, axis=1)
    long.index.names = ["date", "ticker"]
    long["dollar_vol_60"] = f["dollar_vol_60"].stack(future_stack=True)
    long["atr"] = atr.stack(future_stack=True)

    # régimen de mercado (igual para todos los activos en una fecha)
    reg = pd.DataFrame(index=c.index)
    reg["spy_ret_21"] = spy.pct_change(21)
    reg["spy_ret_63"] = spy.pct_change(63)
    reg["spy_above_ma200"] = (spy > spy.rolling(200).mean()).astype(float)
    reg["spy_dist_ma200"] = spy / spy.rolling(200).mean() - 1
    reg["spy_vol_21"] = r_spy.rolling(21).std()
    if vix in p["close"]:
        vx = p["close"][vix]
        reg["vix"] = vx
        reg["vix_chg_5"] = vx.pct_change(5)
        reg["vix_z_252"] = (vx - vx.rolling(252).mean()) / vx.rolling(252).std()
    if tnx in p["close"]:
        reg["tnx_chg_21"] = p["close"][tnx].diff(21)
    reg["breadth_ma50"] = (c > c.rolling(50).mean()).mean(axis=1)   # % de activos sobre su MM50
    long = long.join(reg, on="date")
    return long


# ----------------------------------------------------------------- etiquetas
def triple_barrier(p: dict, assets: list[str], horizon=15, tp_atr=2.0, sl_atr=1.0, bench="SPY") -> pd.DataFrame:
    """Entrada en la APERTURA de t+1. TP/SL en múltiplos del ATR14 calculado al cierre de t.
    Si TP y SL se tocan el mismo día se asume SL (conservador). Gaps: se sale a la apertura.
    Devuelve: y_tb (1 = TP primero), ret_tb (retorno bruto de la operación), k_exit (días), t1 (fecha de salida),
    y_rel (1 = el activo supera a SPY de apertura t+1 a cierre t+H)."""
    o, h, l, c = (p[k][assets] for k in ["open", "high", "low", "close"])
    atr = _atr(h, l, c)
    dates = c.index
    O, H, L, C, A = (x.values for x in (o, h, l, c, atr))
    T, N = C.shape
    entry = np.full((T, N), np.nan)
    entry[:-1] = O[1:]
    tp = entry + tp_atr * A
    sl = entry - sl_atr * A

    exit_px = np.full((T, N), np.nan)
    k_exit = np.full((T, N), np.nan)
    label = np.full((T, N), np.nan)
    done = np.zeros((T, N), bool)
    for k in range(1, horizon + 1):
        if k >= T:
            break
        ok = np.zeros(T, bool)
        ok[: T - k] = True
        Ok = np.full((T, N), np.nan); Ok[: T - k] = O[k:]
        Hk = np.full((T, N), np.nan); Hk[: T - k] = H[k:]
        Lk = np.full((T, N), np.nan); Lk[: T - k] = L[k:]
        Ck = np.full((T, N), np.nan); Ck[: T - k] = C[k:]
        open_ref = Ok if k > 1 else entry   # el día 1 la apertura ES la entrada
        hit_sl = (~done) & (Lk <= sl)
        hit_tp = (~done) & (Hk >= tp) & ~hit_sl
        # salida por SL (gap en contra -> apertura)
        px_sl = np.where(open_ref <= sl, open_ref, sl)
        px_tp = np.where(open_ref >= tp, open_ref, tp)
        exit_px = np.where(hit_sl, px_sl, exit_px)
        exit_px = np.where(hit_tp, px_tp, exit_px)
        label = np.where(hit_sl, 0, label)
        label = np.where(hit_tp, 1, label)
        k_exit = np.where(hit_sl | hit_tp, k, k_exit)
        done |= (hit_sl | hit_tp)
        if k == horizon:   # barrera vertical
            last = (~done) & ~np.isnan(Ck)
            exit_px = np.where(last, Ck, exit_px)
            label = np.where(last, 0, label)
            k_exit = np.where(last, k, k_exit)
            done |= last

    valid = ~np.isnan(entry) & ~np.isnan(A) & ~np.isnan(label)
    ret = np.where(valid, exit_px / entry - 1, np.nan)
    label = np.where(valid, label, np.nan)

    # retorno relativo a SPY a horizonte fijo
    spy_o, spy_c = p["open"][bench].values, p["close"][bench].values
    fwd_asset = np.full((T, N), np.nan)
    fwd_spy = np.full(T, np.nan)
    if T > horizon:
        fwd_asset[: T - horizon] = C[horizon:] / O[1: T - horizon + 1] - 1
        fwd_spy[: T - horizon] = spy_c[horizon:] / spy_o[1: T - horizon + 1] - 1
    y_rel = np.where(np.isnan(fwd_asset), np.nan, (fwd_asset > fwd_spy[:, None]).astype(float))

    kx = np.nan_to_num(k_exit, nan=0).astype(int)
    t1_idx = np.clip(np.arange(T)[:, None] + kx, 0, T - 1)
    t1 = np.where(valid, dates.values[t1_idx], np.datetime64("NaT"))

    def S(a):
        return pd.DataFrame(a, index=dates, columns=assets).stack(future_stack=True)

    out = pd.concat({"y_tb": S(label), "ret_tb": S(ret), "k_exit": S(k_exit),
                     "y_rel": S(y_rel), "fwd_ret_h": S(fwd_asset),
                     "entry_px": S(entry), "tp_px": S(tp), "sl_px": S(sl)}, axis=1)
    out["t1"] = pd.DataFrame(t1, index=dates, columns=assets).stack(future_stack=True)
    out.index.names = ["date", "ticker"]
    return out

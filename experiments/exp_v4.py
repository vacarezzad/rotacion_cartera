"""v4: estrategias documentadas en la literatura / comunidad quant, testeadas con el mismo motor honesto
(señal al cierre, ejecución a la apertura siguiente, costos IOL por lado, liquidez USD al 0%).

A  QQQ / SPY + estructura temporal del VIX: invertido si VIX/VIX3M < 1 (contango), USD si hay backwardation.
B  Connors RSI(2) en QQQ / SPY: compra si cierre > MM200 y RSI2 < 10; sale cuando cierre > MM5.
C  Turn-of-the-month en SPY / QQQ: invertido del último día hábil del mes a los 3 primeros del siguiente.
D  Rotación E4 (regresión 63d, top-5 acciones, trimestral) + overlay VIX/VIX3M (sale a USD en backwardation).
E  QQQ con cobertura de crash solo extrema: sale si VIX/VIX3M > 1,05 y QQQ bajo MM200; vuelve con ambos OK.

Cada estrategia se corre con costos IOL (x1), con costos de un broker barato (x0,3 ≈ 0,28%/lado) y sin costos,
para separar "no hay señal" de "la señal existe pero el costo la mata".
Uso: python exp_v4.py --data data_etf --preds results_v3/preds_cache.pkl --out results_v4
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
from tml import data as D, features as F, backtest as B
from exp_v3 import topk_picks
from exp_etf import stats

STOCKS = ["NVDA", "GOOGL", "AMD", "QQQ", "MELI", "VIST", "META", "MSFT", "AMZN", "GLD", "AVGO", "MCD",
          "TSM", "ORCL", "TSLA"]


def log(*a):
    print(*a, flush=True)


def run_daily(target_fn, p, assets, start, end, cfg, cost_mult=1.0):
    """target_fn(d) -> Series de pesos objetivo (o None = no cambiar), decidido al cierre de d,
    ejecutado a la apertura siguiente. Liquidez no invertida en USD al 0%."""
    O = p["open"][assets]
    allD = O.index[(O.index >= start) & (O.index <= end)]
    c = cfg.cost_per_side * cost_mult
    w = pd.Series(0.0, index=assets)
    val, prev, pending, eq, to_tot, n_tr = 1.0, None, None, [], 0.0, 0
    for d in allD:
        o = O.loc[d]
        if prev is not None:
            r = (o / prev - 1).fillna(0)
            g = 1 + (w * r).sum()
            val *= g
            w = w * (1 + r) / g
        if pending is not None:
            to = (pending - w).abs().sum()
            if to > 1e-9:
                val *= 1 - to * c
                to_tot += to
                n_tr += 1
            w, pending = pending, None
        prev = o
        eq.append((d, val))
        t = target_fn(d)
        if t is not None:
            pending = t.reindex(assets).fillna(0.0)
    e = pd.Series(dict(eq)).sort_index()
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    return e, to_tot / yrs, n_tr / yrs


def state_strategy(signal: pd.Series, asset: str):
    """signal booleano diario (True = invertido). Devuelve target_fn que solo opera en los cambios."""
    last = {"s": None}

    def fn(d):
        s = bool(signal.get(d, False))
        if s == last["s"]:
            return None
        last["s"] = s
        return pd.Series({asset: 1.0 if s else 0.0})
    return fn


def rsi(c, n=2):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def rsi2_signal(c):
    ma200, ma5, r2 = c.rolling(200).mean(), c.rolling(5).mean(), rsi(c, 2)
    inpos, out = False, []
    for d in c.index:
        if not inpos and c[d] > ma200[d] and r2[d] < 10:
            inpos = True
        elif inpos and c[d] > ma5[d]:
            inpos = False
        out.append(inpos)
    return pd.Series(out, index=c.index)


def tom_signal(idx):
    s = pd.Series(idx, index=idx)
    g = s.groupby([idx.year, idx.month])
    pos = g.cumcount()
    from_end = g.cumcount(ascending=False)
    # al cierre de d decidimos la posición de mañana: entrar al cierre del penúltimo día (ejecuta a la
    # apertura del último), salir al cierre del 3er día hábil del mes siguiente
    return pd.Series((from_end <= 1) | (pos <= 1), index=idx)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data_etf")
    ap.add_argument("--preds", default="results_v3/preds_cache.pkl")
    ap.add_argument("--out", default="results_v4")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cfg = Config()
    tick = list(dict.fromkeys(STOCKS + ["SPY", "^VIX", "^VIX3M"]))
    p = D.download(tick, "1998-01-01", None, a.data)
    C = p["close"]
    ratio = (C["^VIX"] / C["^VIX3M"]).ffill()
    vstart = ratio.dropna().index[0] + pd.Timedelta(days=5)
    end = C.index[-1]
    log(f"[v4] VIX3M desde {ratio.dropna().index[0].date()}, datos hasta {end.date()}")

    strategies = {}
    for t in ["QQQ", "SPY"]:
        c = C[t]
        strategies[f"A {t} + VIX/VIX3M<1"] = (lambda t=t: state_strategy(ratio < 1.0, t), [t], vstart)
        strategies[f"B {t} Connors RSI2"] = (lambda t=t, c=c: state_strategy(rsi2_signal(c.dropna()), t), [t],
                                             pd.Timestamp("2001-01-01"))
        strategies[f"C {t} turn-of-month"] = (lambda t=t: state_strategy(tom_signal(C.index), t), [t],
                                              pd.Timestamp("2001-01-01"))
        crash = (ratio > 1.05) & (c < c.rolling(200).mean())
        strategies[f"E {t} solo cubre crash extremo"] = (lambda t=t, crash=crash: state_strategy(~crash, t),
                                                         [t], vstart)

    # D: rotación E4 con overlay de VIX
    P = pd.read_pickle(a.preds)
    S = P["reg63"].unstack().reindex(columns=STOCKS)
    qe = pd.Series(S.index, index=S.index).groupby([S.index.year, S.index.month]).last()
    qe = set(pd.DatetimeIndex(qe.values)[::3])

    def make_D(use_overlay):
        st = {"held": [], "on": None}

        def fn(d):
            changed = False
            if d in qe and d in S.index:
                picks = topk_picks(S.loc[d], st["held"], 5)
                if picks != st["held"]:
                    st["held"], changed = picks, True
            on = (not use_overlay) or bool(ratio.get(d, 0) < 1.0)
            if on != st["on"]:
                st["on"], changed = on, True
            if not changed or not st["held"]:
                return None
            w = pd.Series(1.0 / len(st["held"]), index=st["held"])
            return w if on else w * 0
        return fn
    d0 = max(S.dropna(how="all").index[0], vstart)
    strategies["D rotación E4 sin overlay"] = (lambda: make_D(False), STOCKS, d0)
    strategies["D rotación E4 + VIX/VIX3M"] = (lambda: make_D(True), STOCKS, d0)

    rows, curves = [], {}
    for n, (mk, assets, s0) in strategies.items():
        for cm, lab in [(1.0, "IOL"), (0.3, "broker barato"), (0.0, "sin costos")]:
            e, to, ntr = run_daily(mk(), p, assets, s0, end, cfg, cost_mult=cm)
            curves[f"{n} [{lab}]"] = e
            bench_t = assets[0] if len(assets) == 1 else None
            spy = B.buy_and_hold(p, ["SPY"], e.index[0], end)
            row = {"estrategia": n, "costos": lab, "desde": e.index[0].date(), **stats(e, spy),
                   "operaciones_año": ntr, "turnover_anual": to}
            if bench_t:
                bh = B.buy_and_hold(p, [bench_t], e.index[0], end)
                sb = stats(bh, spy)
                row.update({"B&H_CAGR": sb["CAGR"], "B&H_Sharpe": sb["Sharpe"], "B&H_MaxDD": sb["MaxDD"]})
            else:
                bh = B.buy_and_hold(p, STOCKS, e.index[0], end)
                sb = stats(bh, spy)
                row.update({"B&H_CAGR": sb["CAGR"], "B&H_Sharpe": sb["Sharpe"], "B&H_MaxDD": sb["MaxDD"]})
            rows.append(row)
        log(f"  {n}: listo")
    res = pd.DataFrame(rows)
    res.to_csv(f"{a.out}/v4_resumen.csv", index=False)
    pd.DataFrame(curves).to_csv(f"{a.out}/v4_curvas.csv")
    pd.set_option("display.width", 280)
    pd.set_option("display.max_columns", 30)
    cols = ["estrategia", "costos", "desde", "CAGR", "Sharpe", "MaxDD", "B&H_CAGR", "B&H_Sharpe", "B&H_MaxDD",
            "operaciones_año", "CAGR_08_12", "CAGR_13_19", "CAGR_20_26"]
    log(res[cols].round(3).to_string(index=False))
    log(f"VIX/VIX3M hoy: {ratio.iloc[-1]:.3f}")


if __name__ == "__main__":
    main()

"""¿Conviene bajar el peso de las acciones con semáforo rojo? Universo 30, 2012–2026, neto IOL.

Semáforo histórico point-in-time (solo EDGAR con fecha de presentación; los datos de analistas no tienen historia):
  negativos: calidad compuesta en el 40% inferior · margen operativo TTM < 0 · ganancia neta TTM < 0
  positivos: calidad compuesta en el 40% superior
  rojo = 2+ negativos, o 1 negativo sin positivos (misma regla que make_signal)
Variantes sobre T1 (0,75·E4 + 0,25·sorpresa), top 5:
  A base 20% c/u · B rojo 10%, sobrante a caja USD · C rojo 10%, sobrante al resto · D rojo afuera (entra el siguiente no rojo)
Uso: python exp_red.py --data data_etf --edgar data_etf/edgar   (cwd = carpeta con results_30f/preds30f.pkl)
"""
import argparse, os, sys, warnings
warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tml.config import Config
from tml import data as D, backtest as B
from tml.edgar import edgar_panel
from exp_v3 import run_weights, Strat, topk_picks
from exp_30 import U30
from exp_30f import earnings_features
from exp_30e import perf, xr

QUALITY = {"f_accruals": -1, "f_lev": -1, "f_fcf_yield": 1, "f_rev_g": 1, "f_om_chg": 1, "f_gm": 1}


class RedStrat(Strat):
    def __init__(self, score, red, mode, k=5):
        super().__init__(score, k=k)
        self.red, self.mode, self.log = red, mode, []

    def __call__(self, d):
        if d not in self.score.index:
            return None
        row = self.score.loc[d]
        rr = self.red.loc[d] if d in self.red.index else pd.Series(False, index=row.index)
        if self.mode == "D":
            ok = row[~rr.reindex(row.index).fillna(False).astype(bool)]
            picks = topk_picks(ok, self.held, self.k)
        else:
            picks = topk_picks(row, self.held, self.k)
        self.held = picks
        w = pd.Series(1.0 / len(picks), index=picks)
        isred = rr.reindex(picks).fillna(False).astype(bool)
        self.log.append((d, int(isred.sum())))
        if self.mode in ("B", "C") and isred.any():
            w[isred] = 0.10
            if self.mode == "C" and (~isred).any():
                w[~isred] += (1 - w.sum()) / (~isred).sum()
        return w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data_etf"); ap.add_argument("--edgar", default="data_etf/edgar")
    a = ap.parse_args()
    cfg = Config()
    p = D.download(U30 + ["SPY"], "2003-01-01", None, a.data)
    C = p["close"][U30]
    EP = edgar_panel(a.edgar, U30, C)
    P = pd.read_pickle("results_30f/preds30f.pkl")[["reg63"]]
    E = earnings_features(os.path.join(a.data, "earnings"), U30, p["close"].index)
    F = EP.reindex(P.index)
    q = sum(sgn * (xr(F[c]) - 0.5) for c, sgn in QUALITY.items()) / len(QUALITY)
    q = xr(q.where(F[list(QUALITY)].notna().sum(axis=1) >= 3))
    neg = (q <= 0.4).astype(int) + (F["f_om"] < 0).astype(int) + (F["f_ey"] < 0).astype(int)
    pos = (q >= 0.6).astype(int)
    red = ((neg >= 2) | ((neg >= 1) & (pos == 0))) & q.notna()
    R = red.unstack().reindex(columns=U30).fillna(False)
    score = (0.75 * xr(P["reg63"]) + 0.25 * E["xr_e_surp"].reindex(P.index).fillna(0.5)).unstack().reindex(columns=U30)
    start = P.index.get_level_values("date").min()
    bh = B.buy_and_hold(p, U30, start, p["close"].index[-1])
    print("% de acciones en rojo por fecha (promedio):", round(float(R.loc[start:].mean(axis=1).mean()), 3))
    rows, curves = [{"variante": "B&H EW30", **perf(bh, bh)}], {"B&H EW30": bh}
    names = {"A": "A base 20% c/u", "B": "B rojo 10%, resto a caja", "C": "C rojo 10%, resto al resto", "D": "D rojo afuera"}
    for m, n in names.items():
        st = RedStrat(score, R, m)
        e, _ = run_weights(st, p, U30, start, cfg, every=3)
        lg = pd.DataFrame(st.log, columns=["d", "n"])
        rows.append({"variante": n, **perf(e, bh), "rojos_por_reb": round(lg.n.mean(), 2)})
        curves[n] = e
        # retorno forward de los picks rojos vs no rojos (63d) en la base
    res = pd.DataFrame(rows)
    os.makedirs("results_red", exist_ok=True)
    res.to_csv("results_red/red_resumen.csv", index=False)
    pd.DataFrame(curves).to_csv("results_red/red_curvas.csv")
    pd.set_option("display.width", 220)
    print(res.round(3).to_string(index=False))
    # ¿Rinden peor los picks rojos? exceso a 63 días vs mediana, entre top 5 por fecha de rebalanceo
    o = p["open"][U30]
    f = (o.shift(-64) / o.shift(-1) - 1)
    ex = f.sub(f.median(axis=1), axis=0)
    rebs = [d for d in score.index if d.month in (1, 4, 7, 10) and d == score.index[score.index.to_period("M") == d.to_period("M")].max()]
    rr, nr = [], []
    for d in rebs:
        top = score.loc[d].dropna().sort_values(ascending=False).index[:5]
        for t in top:
            v = ex.loc[d, t] if d in ex.index else np.nan
            if pd.isna(v):
                continue
            (rr if R.loc[d, t] else nr).append(v)
    print(f"picks rojos: n={len(rr)} exceso medio 63d {np.mean(rr):+.3%} (mediana {np.median(rr):+.3%})")
    print(f"picks no rojos: n={len(nr)} exceso medio 63d {np.mean(nr):+.3%} (mediana {np.median(nr):+.3%})")


if __name__ == "__main__":
    main()
